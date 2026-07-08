from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions, status as http_status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from .models import Company, Mpin, StaffAssignment, StaffRole
from .permissions import IsAdminOrStaff
from ticketapp.models import Ticket
from .serializers import (
    CompanyDetailSerializer,
    CompanyDraftSerializer,
    CompanyListSerializer,
    CompanyRejectSerializer,
    CompanySubmitSerializer,
    LoginInputSerializer,
    MpinCreateSerializer,
    RoleDetectSerializer,
    StaffCreateSerializer,
    StaffListSerializer,
    StaffRoleSerializer,
    StaffUpdateSerializer,
    CustomerListSerializer,
    CustomerDetailSerializer,
    CustomerUpdateSerializer,
    ProductVerificationSerializer,
    StaffAssignmentSaveSerializer,
    StaffAssignedCustomerSerializer,
)
from .utils import (
    generate_company_code,
    send_approval_email,
    send_registration_received_email,
    send_rejection_email,
)

User = get_user_model()


def issue_tokens(user):
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "role": user.role,
        "phone_number": user.phone_number,
        "full_name": user.full_name,
    }


class LoginView(APIView):
    """
    POST { phone_number, password }  -> password login (works every time; also used first-time)
    POST { phone_number, mpin }      -> M-PIN login (only works once an M-PIN exists)

    Responses:
      { access, refresh, role, phone_number, full_name }   success
      { mpin_required: true }                              password correct, no M-PIN set yet
      { detail: "pending_approval" }                        account not yet approved
      { detail: "account_deactivated" }                     account has been deactivated
      { detail: "..." }                                     invalid credentials
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        phone_number = data["phone_number"]
        password = data.get("password")
        mpin = data.get("mpin")

        user = User.objects.filter(phone_number=phone_number).first()
        if not user:
            return Response({"detail": "Incorrect phone number or credentials."}, status=400)

        if mpin:
            if not hasattr(user, "mpin"):
                return Response(
                    {"detail": "M-PIN not set for this account. Please log in with your password."},
                    status=400,
                )
            if not user.mpin.check_mpin(mpin):
                return Response({"detail": "Incorrect phone number or M-PIN."}, status=400)
        else:
            if not user.check_password(password):
                return Response({"detail": "Incorrect phone number or password."}, status=400)

        if not user.is_active:
            return Response({"detail": "account_deactivated"}, status=403)

        if not user.is_approved:
            return Response({"detail": "pending_approval"}, status=403)

        if not hasattr(user, "mpin"):
            # Password was correct, but this is a first-time login.
            return Response({"mpin_required": True})

        return Response(issue_tokens(user))


class CreateMpinView(APIView):
    """POST { phone_number, password, mpin, confirm_mpin }
    Re-verifies the password and creates the M-PIN. Does NOT log the user
    in — they must log in again using the new M-PIN."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = MpinCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = User.objects.filter(phone_number=data["phone_number"]).first()
        if not user or not user.check_password(data["password"]):
            return Response({"detail": "Invalid request."}, status=400)

        if not user.is_approved:
            return Response({"detail": "pending_approval"}, status=403)

        mpin_obj, _ = Mpin.objects.get_or_create(user=user)
        mpin_obj.set_mpin(data["mpin"])

        return Response({
            "success": True,
            "detail": "M-PIN created successfully. Please log in with your M-PIN.",
        })


class DetectRoleView(APIView):
    """POST { phone_number } -> { exists, role }"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RoleDetectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.to_role_response())


# ---------------------------------------------------------------------------
# Onboarding — public draft/submit endpoints
# ---------------------------------------------------------------------------

class OnboardingDraftView(APIView):
    """
    POST { draft_token?, ...form fields, products: [...] }
    Upserts a Company in DRAFT status. No account is created here.
    Response: { draft_token }

    NOTE: unused by the current frontend (Save as Draft was removed from
    the UI), left in place in case you want to reintroduce drafts later.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CompanyDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        company = serializer.save_draft()
        return Response({"draft_token": company.draft_token}, status=200)


class OnboardingSubmitView(APIView):
    """
    POST { ...all form fields, password, confirm_password, products: [...] }
    Validates required fields, creates the (unapproved) CustomUser account
    using the password supplied in the form, finalises the Company as
    PENDING, and emails a "received" confirmation.
    """
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        serializer = CompanySubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = dict(serializer.validated_data)

        products_data = validated.pop("products", [])
        draft_token = validated.pop("draft_token", None)
        password = validated.pop("password")
        validated.pop("confirm_password")

        company = None
        if draft_token:
            company = Company.objects.filter(draft_token=draft_token).first()

        if company:
            for field, value in validated.items():
                setattr(company, field, value)
        else:
            company = Company(**validated)

        company.company_code = generate_company_code()
        company.status = Company.Status.PENDING
        company.submitted_at = timezone.now()

        # Create the linked (unapproved) account with the password the
        # customer set on the form. Login uses mobile number, matching the
        # existing auth system's USERNAME_FIELD.
        user = User.objects.create_user(
            phone_number=company.mobile_number,
            password=password,
            full_name=company.contact_name,
            role=User.Role.CUSTOMER,
        )
        company.user = user
        company.save()

        company.products.all().delete()
        for product in products_data:
            company.products.create(**product)

        send_registration_received_email(company)

        return Response(
            {
                "company_code": company.company_code,
                "status": company.status,
                "message": "Registration submitted. Your account is pending admin approval.",
            },
            status=201,
        )


# ---------------------------------------------------------------------------
# Onboarding — admin/staff review endpoints
# ---------------------------------------------------------------------------

class PendingCompanyListView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request):
        companies = Company.objects.filter(status=Company.Status.PENDING).order_by("-submitted_at")
        return Response(CompanyListSerializer(companies, many=True).data)


class CompanyDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request, company_id):
        company = Company.objects.filter(id=company_id).first()
        if not company:
            return Response({"detail": "Not found."}, status=404)
        return Response(CompanyDetailSerializer(company).data)


class ApproveCompanyView(APIView):
    """The customer already set their own password at signup, so approval
    just unlocks login — no password is generated or emailed here."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, company_id):
        company = Company.objects.filter(id=company_id, status=Company.Status.PENDING).first()
        if not company:
            return Response({"detail": "Pending registration not found."}, status=404)
        if not company.user:
            return Response({"detail": "No linked account for this registration."}, status=400)

        company.user.is_approved = True
        company.user.save()

        company.status = Company.Status.APPROVED
        company.reviewed_at = timezone.now()
        company.reviewed_by = request.user
        company.save()

        send_approval_email(company)

        return Response({"detail": "Company approved. The customer can now log in."})


class RevokeCompanyApprovalView(APIView):
    """Reverses an approval: sends the company back to PENDING and blocks
    the linked user from logging in again until re-approved. Use this
    instead of hand-editing is_approved in the DB, since that leaves the
    Company row silently out of sync with the pending queue."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, company_id):
        company = Company.objects.filter(id=company_id, status=Company.Status.APPROVED).first()
        if not company:
            return Response({"detail": "Approved registration not found."}, status=404)
        if not company.user:
            return Response({"detail": "No linked account for this registration."}, status=400)

        company.user.is_approved = False
        company.user.save()

        company.status = Company.Status.PENDING
        company.reviewed_at = None
        company.reviewed_by = None
        company.save()

        return Response({"detail": "Approval revoked. This registration is back in the pending queue."})


class RejectCompanyView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, company_id):
        company = Company.objects.filter(id=company_id, status=Company.Status.PENDING).first()
        if not company:
            return Response({"detail": "Pending registration not found."}, status=404)

        serializer = CompanyRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")

        company.status = Company.Status.REJECTED
        company.reviewed_at = timezone.now()
        company.reviewed_by = request.user
        company.save()

        if company.user:
            company.user.is_active = False
            company.user.save()

        send_rejection_email(company, reason)

        return Response({"detail": "Company rejected."})


class MyProductsView(APIView):
    """
    GET /my-products/
    Returns the products THIS customer's company had verified/approved by
    an admin during Account Approvals (Step B) — used to populate the
    Product/Module dropdown on Raise New Ticket.

    Staff/admin, or a customer whose company isn't approved yet, get an
    empty list; the frontend always adds "Not Applicable" itself regardless.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        if getattr(user, 'role', None) != User.Role.CUSTOMER:
            return Response({"products": []})

        company = getattr(user, 'company', None)
        if not company or company.status != Company.Status.APPROVED:
            return Response({"products": []})

        verification = company.product_verification or {}
        approved = [
            name for name in company.products_in_use
            if verification.get(name) == "Verified"
        ]
        return Response({"products": approved})


class AssignStaffView(APIView):
    """POST { mode, primary_staff_ids? , per_product? }
    Saves Step C's staff assignment for a company. History is kept: any
    previously-current rows for this company are marked is_current=False
    rather than deleted, then fresh rows are inserted — one row per staff
    per target, so multiple staff can share the same product/company."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, company_id):
        company = Company.objects.filter(id=company_id).first()
        if not company:
            return Response({"detail": "Not found."}, status=404)

        serializer = StaffAssignmentSaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data["mode"] == "primary":
            staff_ids = list(dict.fromkeys(data["primary_staff_ids"]))
        else:
            staff_ids = list(dict.fromkeys(
                sid for ids in data["per_product"].values() for sid in ids
            ))

        valid_staff = set(
            User.objects.filter(id__in=staff_ids, role=User.Role.STAFF).values_list("id", flat=True)
        )
        if set(staff_ids) - valid_staff:
            return Response({"detail": "One or more selected staff members are invalid."}, status=400)

        with transaction.atomic():
            company.staff_assignments.filter(is_current=True).update(is_current=False)

            if data["mode"] == "primary":
                for staff_id in dict.fromkeys(data["primary_staff_ids"]):
                    StaffAssignment.objects.create(
                        company=company,
                        staff_id=staff_id,
                        product_name="",
                        assigned_by=request.user,
                    )
            else:
                for product_name, ids in data["per_product"].items():
                    for staff_id in dict.fromkeys(ids):
                        StaffAssignment.objects.create(
                            company=company,
                            staff_id=staff_id,
                            product_name=product_name,
                            assigned_by=request.user,
                        )

        return Response(CompanyDetailSerializer(company).data)


class VerifyProductsView(APIView):
    """POST { product_verification: {name: status}, verification_note }
    Saves Step B's per-product verification and internal note against the
    Company. Doesn't require status=PENDING, so admins can revisit/update
    this even after later steps."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, company_id):
        company = Company.objects.filter(id=company_id).first()
        if not company:
            return Response({"detail": "Not found."}, status=404)

        serializer = ProductVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if "product_verification" in data:
            company.product_verification = data["product_verification"]
        if "verification_note" in data:
            company.verification_note = data["verification_note"]
        company.save()

        return Response(CompanyDetailSerializer(company).data)


# ---------------------------------------------------------------------------
# Staff Management
# ---------------------------------------------------------------------------

class StaffListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request):
        staff = User.objects.filter(role=User.Role.STAFF).select_related("designation").order_by("full_name")
        return Response(StaffListSerializer(staff, many=True).data)

    def post(self, request):
        serializer = StaffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(StaffListSerializer(user).data, status=201)


class StaffDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def patch(self, request, staff_id):
        user = User.objects.filter(id=staff_id, role=User.Role.STAFF).first()
        if not user:
            return Response({"detail": "Staff member not found."}, status=404)
        serializer = StaffUpdateSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(StaffListSerializer(user).data)

    def delete(self, request, staff_id):
        user = User.objects.filter(id=staff_id, role=User.Role.STAFF).first()
        if not user:
            return Response({"detail": "Staff member not found."}, status=404)

        has_active_assignments = StaffAssignment.objects.filter(
            staff_id=staff_id,
            is_current=True,
            company__status=Company.Status.APPROVED,
        ).exists()

        if has_active_assignments:
            return Response(
                {
                    "detail": "This staff member is currently assigned to one or more "
                              "customers. Reassign those customers to someone else before "
                              "deleting this account."
                },
                status=400,
            )

        user.delete()
        return Response(status=204)


class StaffToggleStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, staff_id):
        user = User.objects.filter(id=staff_id, role=User.Role.STAFF).first()
        if not user:
            return Response({"detail": "Staff member not found."}, status=404)
        user.is_active = not user.is_active
        user.save()
        return Response(StaffListSerializer(user).data)


class StaffAssignedCustomersView(APIView):
    """GET — companies currently assigned to this staff member (primary or
    per-product), used by the Staff Management detail slide-over."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request, staff_id):
        user = User.objects.filter(id=staff_id, role=User.Role.STAFF).first()
        if not user:
            return Response({"detail": "Staff member not found."}, status=404)

        assignments = (
            StaffAssignment.objects
            .filter(staff_id=staff_id, is_current=True, company__status=Company.Status.APPROVED)
            .select_related("company")
            .order_by("company__company_name")
        )
        return Response(StaffAssignedCustomerSerializer(assignments, many=True).data)


class StaffRoleListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request):
        return Response(StaffRoleSerializer(StaffRole.objects.all(), many=True).data)

    def post(self, request):
        serializer = StaffRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"]
        if StaffRole.objects.filter(name__iexact=name).exists():
            return Response({"detail": "This role already exists."}, status=400)
        serializer.save()
        return Response(serializer.data, status=201)


class StaffRoleDeleteView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def delete(self, request, role_id):
        role = StaffRole.objects.filter(id=role_id).first()
        if not role:
            return Response({"detail": "Role not found."}, status=404)
        role.delete()
        return Response(status=204)


# ---------------------------------------------------------------------------
# Customer Management (Admin/Staff)
# ---------------------------------------------------------------------------

class CustomerListView(generics.ListAPIView):
    """
    GET /customers/?search=...&status=active|pending|blocked
    Admin/staff only. Lists all customer-role accounts with search + filter.
    """
    serializer_class = CustomerListSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def get_queryset(self):
        qs = User.objects.filter(role=User.Role.CUSTOMER).select_related("company")
        params = self.request.query_params

        search = params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone_number__icontains=search)
                | Q(company__company_name__icontains=search)
            )

        status_param = params.get("status")
        if status_param == "active":
            qs = qs.filter(is_active=True, is_approved=True)
        elif status_param == "pending":
            qs = qs.filter(is_approved=False, is_active=True)
        elif status_param == "blocked":
            qs = qs.filter(is_active=False)

        return qs.order_by("-date_joined")


class CustomerDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /customers/<id>/   — full detail for the View panel
    PATCH  /customers/<id>/   — Edit action (name/email/phone only)
    DELETE /customers/<id>/   — Delete action. Blocked with 409 if the
                                 customer has raised even a single ticket;
                                 use Deactivate instead in that case.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    queryset = User.objects.filter(role=User.Role.CUSTOMER).select_related("company")

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return CustomerUpdateSerializer
        return CustomerDetailSerializer

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # Respond with the full detail shape so the frontend can refresh the row in-place.
        return Response(CustomerDetailSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        if Ticket.objects.filter(raised_by=instance).exists():
            return Response(
                {
                    "detail": "This customer has raised one or more tickets and cannot be "
                              "deleted. Deactivate the account instead if you need to "
                              "restrict access."
                },
                status=http_status.HTTP_409_CONFLICT,
            )

        instance.delete()
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class CustomerDeactivateView(APIView):
    """
    PATCH /customers/<id>/deactivate/
    Toggles is_active. A blocked customer is reactivated by calling this
    again (the frontend button label flips between "Deactivate" / "Activate").
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def patch(self, request, pk):
        try:
            customer = User.objects.get(pk=pk, role=User.Role.CUSTOMER)
        except User.DoesNotExist:
            return Response({"detail": "Customer not found."}, status=http_status.HTTP_404_NOT_FOUND)

        customer.is_active = not customer.is_active
        customer.save(update_fields=["is_active"])
        return Response(CustomerDetailSerializer(customer).data)


class LogoutView(APIView):
    """POST { refresh } -> blacklists the refresh token so it can't be
    used again, even if someone has a copy of it. Requires the access
    token to still be valid (IsAuthenticated), which is normally the case
    since this runs right before the user is kicked to /login/."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response({"detail": "Refresh token is required."}, status=400)

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return Response({"detail": "Invalid or already-expired token."}, status=400)

        return Response({"detail": "Logged out successfully."})