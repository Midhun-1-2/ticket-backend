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
import random
from datetime import timedelta
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from .models import Company, EmailOTP, Mpin, Product, StaffAssignment, StaffRole, StaffDepartment
from .permissions import IsAdminOrStaff
from .email_templates import build_otp_email_html, build_otp_email_text
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
    StaffDepartmentSerializer,
    StaffUpdateSerializer,
    CustomerListSerializer,
    CustomerDetailSerializer,
    CustomerUpdateSerializer,
    CustomerAddProductSerializer,
    ProductSerializer,
    ProductVerificationSerializer,
    StaffAssignmentSaveSerializer,
    StaffAssignedCustomerSerializer,
    StaffAssignedTicketSerializer,
    ProfileSerializer,
    VerifyMpinOtpSerializer,
    ChangeMpinSerializer,
    ForgotMpinRequestOtpSerializer,
    ForgotMpinVerifyOtpSerializer,
    ForgotMpinResetSerializer,
)
from .utils import (
    generate_company_code,
    send_approval_email,
    send_registration_received_email,
    send_rejection_email,
)
from ticketapp.models import ProductMaster, Ticket

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


def send_otp_email(user, otp, title, intro_text):
    """Shared by RequestMpinChangeOtpView and RequestForgotMpinOtpView —
    sends the branded HTML OTP email (see authentication/email_templates.py)
    with a plain-text fallback for clients that don't render HTML."""
    text_body = build_otp_email_text(user.full_name or user.phone_number, otp, title, intro_text)
    html_body = build_otp_email_html(user.full_name or user.phone_number, otp, title, intro_text)

    email = EmailMultiAlternatives(
        subject=title,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)


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

class CheckMobileAvailabilityView(APIView):
    """
    GET /check-mobile/?mobile_number=9845021190
    Public, read-only pre-check used by the onboarding form's Primary
    Contact step to warn the person a mobile number is already registered
    *before* they reach final submit — where
    CompanySubmitSerializer.validate_mobile_number would otherwise reject
    it with the same message. "Already registered" means a CustomUser row
    already has this phone_number, mirroring that check exactly.

    NOTE: CustomUser.phone_number (and Company.mobile_number) are plain
    10-digit fields with no stored country code — same format
    validate_mobile_number enforces at submit time. `country_code` is
    accepted here for forward-compatibility with the multi-country
    onboarding UI but isn't used in the lookup, since stored numbers
    aren't country-scoped today (see note above).
    """
    permission_classes = [AllowAny]

    def get(self, request):
        mobile_number = (request.query_params.get("mobile_number") or "").strip()
        exists = bool(mobile_number) and User.objects.filter(phone_number=mobile_number).exists()
        return Response({"exists": exists})


# ---------------------------------------------------------------------------
# Onboarding — public draft/submit endpoints
# ---------------------------------------------------------------------------

class OnboardingDraftView(APIView):
    """
    POST { draft_token?, ...form fields, products: [...] }
    Upserts a Company in DRAFT status. No account is created here.
    Response: { draft_token }
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
    the linked user from logging in again until re-approved."""
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
    an admin during Account Approvals (Step B).
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
    Saves Step C's staff assignment for a company."""
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
    """POST { product_verification: {name: status}, verification_note }"""
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
    """GET — companies currently assigned to this staff member."""
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


class StaffAssignedTicketsView(APIView):
    """GET /staff/<id>/assigned-tickets/
    Tickets currently assigned to this staff member (Ticket.assigned_staff).
    Sibling of StaffAssignedCustomersView above — same auth, same 404
    behavior, just sourced from Ticket instead of StaffAssignment. This is
    what the Staff Management detail slide-over calls to populate an
    "Assigned Tickets" section next to "Assigned Customers"."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request, staff_id):
        user = User.objects.filter(id=staff_id, role=User.Role.STAFF).first()
        if not user:
            return Response({"detail": "Staff member not found."}, status=404)

        tickets = (
            Ticket.objects.filter(assigned_staff_id=staff_id)
            .select_related("category", "raised_by")
            .order_by("-created_at")
        )
        return Response(StaffAssignedTicketSerializer(tickets, many=True).data)


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


class StaffDepartmentListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request):
        return Response(StaffDepartmentSerializer(StaffDepartment.objects.all(), many=True).data)

    def post(self, request):
        serializer = StaffDepartmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"]
        if StaffDepartment.objects.filter(name__iexact=name).exists():
            return Response({"detail": "This department already exists."}, status=400)
        serializer.save()
        return Response(serializer.data, status=201)


class StaffDepartmentDeleteView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def delete(self, request, department_id):
        department = StaffDepartment.objects.filter(id=department_id).first()
        if not department:
            return Response({"detail": "Department not found."}, status=404)
        department.delete()
        return Response(status=204)


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


class CustomerDetailView(generics.RetrieveUpdateAPIView):
    """
    GET   /customers/<id>/   — full detail for the View panel
    PATCH /customers/<id>/   — Edit action (name/email/phone only)
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
        return Response(CustomerDetailSerializer(instance).data)


class CustomerDeactivateView(APIView):
    """
    PATCH /customers/<id>/deactivate/
    Toggles is_active.
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


class CustomerAddProductView(APIView):
    """
    POST /customers/<id>/products/  { product_id }
    Admin/staff picks a Product Master catalog entry for a customer who's
    purchased something new since registration. Creates a Product row on
    the customer's company (name/version copied from the catalog,
    activation_date defaults to today), appends the name to
    Company.products_in_use if it isn't already there, and marks it
    "Verified" in product_verification — this admin-initiated add IS the
    verification, unlike onboarding-time products which go through the
    separate Account Approvals review (VerifyProductsView) before a
    customer can raise tickets against them (see MyProductsView, which
    only returns names marked Verified).
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def post(self, request, pk):
        try:
            customer = User.objects.select_related("company").get(pk=pk, role=User.Role.CUSTOMER)
        except User.DoesNotExist:
            return Response({"detail": "Customer not found."}, status=http_status.HTTP_404_NOT_FOUND)

        company = getattr(customer, "company", None)
        if not company:
            return Response(
                {"detail": "This customer has no company profile."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        serializer = CustomerAddProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product_master = ProductMaster.objects.get(pk=serializer.validated_data["product_id"])

        product = Product.objects.create(
            company=company,
            product_name=product_master.name,
            product_version=product_master.version,
            activation_date=timezone.now().date(),
        )

        update_fields = []
        if product_master.name not in company.products_in_use:
            company.products_in_use.append(product_master.name)
            update_fields.append("products_in_use")

        if company.product_verification.get(product_master.name) != "Verified":
            company.product_verification[product_master.name] = "Verified"
            update_fields.append("product_verification")

        if update_fields:
            company.save(update_fields=update_fields)

        return Response(ProductSerializer(product).data, status=http_status.HTTP_201_CREATED)


class CustomerRemoveProductView(APIView):
    """
    DELETE /customers/<id>/products/<product_id>/
    Removes a single Product row from the customer's company. Also drops
    the name from Company.products_in_use and product_verification if no
    other Product row with that name remains for this company.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def delete(self, request, pk, product_id):
        try:
            customer = User.objects.select_related("company").get(pk=pk, role=User.Role.CUSTOMER)
        except User.DoesNotExist:
            return Response({"detail": "Customer not found."}, status=http_status.HTTP_404_NOT_FOUND)

        company = getattr(customer, "company", None)
        if not company:
            return Response(
                {"detail": "This customer has no company profile."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            product = Product.objects.get(pk=product_id, company=company)
        except Product.DoesNotExist:
            return Response({"detail": "Product not found."}, status=http_status.HTTP_404_NOT_FOUND)

        product_name = product.product_name
        product.delete()

        if not company.products.filter(product_name=product_name).exists():
            update_fields = []
            if product_name in company.products_in_use:
                company.products_in_use.remove(product_name)
                update_fields.append("products_in_use")
            if product_name in company.product_verification:
                del company.product_verification[product_name]
                update_fields.append("product_verification")
            if update_fields:
                company.save(update_fields=update_fields)

        return Response(status=http_status.HTTP_204_NO_CONTENT)

class LogoutView(APIView):
    """POST { refresh } -> blacklists the refresh token."""
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
    
class ProfileView(APIView):
    """
    GET   /profile/   — the logged-in user's own basic details
    PATCH /profile/   — edit full_name / email only (see ProfileSerializer)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(ProfileSerializer(request.user).data)

    def patch(self, request):
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProfileSerializer(request.user).data)


class RequestMpinChangeOtpView(APIView):
    """
    POST /mpin/change/request-otp/
    Generates a 4-digit OTP, emails it (branded HTML, see send_otp_email
    above) to the logged-in user's registered email, and stores a hashed
    copy (10 min expiry). Any previous unused OTPs for this user+purpose
    are invalidated first — same "only the latest one is live" pattern as
    flipping stale pending offers in AcceptTicketAssignmentView.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if not user.email:
            return Response(
                {"detail": "No email is registered on this account. Contact an admin to add one."},
                status=400,
            )

        EmailOTP.objects.filter(
            user=user, purpose=EmailOTP.PURPOSE_MPIN_CHANGE, is_used=False
        ).update(is_used=True)

        otp = f"{random.randint(0, 9999):04d}"
        otp_row = EmailOTP(
            user=user,
            purpose=EmailOTP.PURPOSE_MPIN_CHANGE,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        otp_row.set_otp(otp)
        otp_row.save()

        send_otp_email(
            user, otp,
            title="Change your M-PIN",
            intro_text="Use the code below to confirm you'd like to change your M-PIN.",
        )

        return Response({"detail": "OTP sent to your registered email."})


class VerifyMpinChangeOtpView(APIView):
    """POST /mpin/change/verify-otp/  { otp }
    Marks the latest matching OTP row as verified. Doesn't change the
    M-PIN yet — ChangeMpinView below checks for a verified row instead of
    accepting the OTP a second time."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerifyMpinOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp = serializer.validated_data["otp"]

        otp_row = EmailOTP.objects.filter(
            user=request.user, purpose=EmailOTP.PURPOSE_MPIN_CHANGE, is_used=False
        ).order_by("-created_at").first()

        if not otp_row or otp_row.is_expired():
            return Response({"detail": "OTP has expired. Please request a new one."}, status=400)
        if not otp_row.check_otp(otp):
            return Response({"detail": "Incorrect OTP."}, status=400)

        otp_row.is_verified = True
        otp_row.save(update_fields=["is_verified"])

        return Response({"detail": "OTP verified. You can now set a new M-PIN."})


class ChangeMpinView(APIView):
    """POST /mpin/change/  { new_mpin, confirm_mpin }
    Requires a verified, unused, unexpired OTP row for this user (see
    VerifyMpinChangeOtpView above)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangeMpinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_mpin = serializer.validated_data["new_mpin"]

        otp_row = EmailOTP.objects.filter(
            user=request.user,
            purpose=EmailOTP.PURPOSE_MPIN_CHANGE,
            is_used=False,
            is_verified=True,
        ).order_by("-created_at").first()

        if not otp_row or otp_row.is_expired():
            return Response(
                {"detail": "OTP verification required before changing your M-PIN. Please request a new OTP."},
                status=400,
            )

        mpin_obj, _ = Mpin.objects.get_or_create(user=request.user)
        mpin_obj.set_mpin(new_mpin)

        otp_row.is_used = True
        otp_row.save(update_fields=["is_used"])

        return Response({"detail": "M-PIN changed successfully."})


# ---------------------------------------------------------------------------
# Forgot M-PIN — unauthenticated flow from the login screen's
# "Forgot M-PIN?" link. Mirrors RequestMpinChangeOtpView / VerifyMpinChangeOtpView
# / ChangeMpinView above exactly, except:
#   - permission_classes = [AllowAny] instead of [IsAuthenticated] (no
#     session exists yet — that's the whole point of "forgot")
#   - identity comes from phone_number in the request body instead of
#     request.user, since there's no authenticated user to read it from
#   - uses EmailOTP.PURPOSE_MPIN_FORGOT instead of PURPOSE_MPIN_CHANGE, so
#     an in-flight "forgot" OTP and an in-flight "change" OTP (e.g. from a
#     different device where the person IS logged in) never collide or
#     get consumed by the wrong flow
# ---------------------------------------------------------------------------

class RequestForgotMpinOtpView(APIView):
    """
    POST /mpin/forgot/request-otp/  { phone_number }
    Used from the login screen — before any session exists. Identity is
    proven via OTP-to-registered-email rather than a password, since the
    whole point is the person doesn't have their M-PIN handy.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotMpinRequestOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone_number = serializer.validated_data["phone_number"]

        user = User.objects.filter(phone_number=phone_number).first()
        if not user:
            return Response({"detail": "No account found with this phone number."}, status=404)
        if not user.email:
            return Response(
                {"detail": "No email is registered on this account. Contact an admin."},
                status=400,
            )
        if not user.is_active:
            return Response({"detail": "account_deactivated"}, status=403)
        if not user.is_approved:
            return Response({"detail": "pending_approval"}, status=403)

        EmailOTP.objects.filter(
            user=user, purpose=EmailOTP.PURPOSE_MPIN_FORGOT, is_used=False
        ).update(is_used=True)

        otp = f"{random.randint(0, 9999):04d}"
        otp_row = EmailOTP(
            user=user,
            purpose=EmailOTP.PURPOSE_MPIN_FORGOT,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        otp_row.set_otp(otp)
        otp_row.save()

        send_otp_email(
            user, otp,
            title="Reset your M-PIN",
            intro_text="Use the code below to verify it's you, then you'll be able to set a new M-PIN.",
        )

        # Masked so the login screen can show "OTP sent to j***n@gmail.com"
        # without exposing the full address to whoever's at the keyboard.
        masked_email = user.email[0] + "***" + user.email[user.email.index("@"):]
        return Response({"detail": "OTP sent to your registered email.", "masked_email": masked_email})


class VerifyForgotMpinOtpView(APIView):
    """POST /mpin/forgot/verify-otp/  { phone_number, otp }"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotMpinVerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = User.objects.filter(phone_number=data["phone_number"]).first()
        if not user:
            return Response({"detail": "No account found with this phone number."}, status=404)

        otp_row = EmailOTP.objects.filter(
            user=user, purpose=EmailOTP.PURPOSE_MPIN_FORGOT, is_used=False
        ).order_by("-created_at").first()

        if not otp_row or otp_row.is_expired():
            return Response({"detail": "OTP has expired. Please request a new one."}, status=400)
        if not otp_row.check_otp(data["otp"]):
            return Response({"detail": "Incorrect OTP."}, status=400)

        otp_row.is_verified = True
        otp_row.save(update_fields=["is_verified"])

        return Response({"detail": "OTP verified. You can now set a new M-PIN."})


class ResetForgotMpinView(APIView):
    """POST /mpin/forgot/reset/  { phone_number, new_mpin, confirm_mpin }
    Requires a verified, unused, unexpired OTP row for this user (see
    VerifyForgotMpinOtpView above) — same pattern as ChangeMpinView."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotMpinResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = User.objects.filter(phone_number=data["phone_number"]).first()
        if not user:
            return Response({"detail": "No account found with this phone number."}, status=404)

        otp_row = EmailOTP.objects.filter(
            user=user,
            purpose=EmailOTP.PURPOSE_MPIN_FORGOT,
            is_used=False,
            is_verified=True,
        ).order_by("-created_at").first()

        if not otp_row or otp_row.is_expired():
            return Response(
                {"detail": "OTP verification required before resetting your M-PIN. Please request a new OTP."},
                status=400,
            )

        mpin_obj, _ = Mpin.objects.get_or_create(user=user)
        mpin_obj.set_mpin(data["new_mpin"])

        otp_row.is_used = True
        otp_row.save(update_fields=["is_used"])

        return Response({"detail": "M-PIN reset successfully. You can now log in with it."})