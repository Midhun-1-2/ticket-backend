from django.conf import settings
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
from .models import Company, DropdownOption, EmailOTP, Mpin, Product, ReportPasswordSettings, StaffAssignment, StaffProduct, StaffRole, StaffDepartment
from .permissions import IsAdminOrStaff
from .email_templates import (
    build_otp_email_html, build_otp_email_text, company_contact_extra_images, send_branded_email,
)
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
    send_approval_email,
    send_registration_received_email,
    send_rejection_email,
    record_login_activity,
    get_client_ip,
)
from ticketapp.models import ProductMaster, Ticket
from ticketapp.views import release_staff_tickets, restore_staff_eligibility

User = get_user_model()


def issue_tokens(user):
    refresh = RefreshToken.for_user(user)
    # Stamped onto both tokens (access inherits it from the refresh token)
    # so DeviceCheckedJWTAuthentication can tell a superseded session's
    # still-unexpired token apart from the current one — see jwt_auth.py.
    refresh["device_id"] = user.active_device_id or ""
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "role": user.role,
        "phone_number": user.phone_number,
        "full_name": user.full_name,
    }


def send_otp_email(user, otp, title, intro_text):
    """Sends a branded HTML OTP email with a plain-text fallback."""
    text_body = build_otp_email_text(user.full_name or user.phone_number, otp, title, intro_text)
    html_body = build_otp_email_html(user.full_name or user.phone_number, otp, title, intro_text)
    send_branded_email(user.email, title, text_body, html_body, extra_images=company_contact_extra_images())


def inactive_account_response(user):
    """Distinguishes an admin-rejected account from a plain deactivation."""
    company = getattr(user, "company", None)
    if company and company.status == Company.Status.REJECTED:
        return Response(
            {"detail": "account_rejected", "reason": company.rejection_reason or ""},
            status=403,
        )
    return Response({"detail": "account_deactivated"}, status=403)


class LoginView(APIView):
    """Login by password or M-PIN."""
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
            record_login_activity(
                request, phone_number=phone_number, status="failed",
                failure_reason="No account with this phone number",
            )
            return Response({"detail": "Incorrect phone number or credentials."}, status=400)

        if mpin:
            if not hasattr(user, "mpin"):
                record_login_activity(
                    request, user=user, status="failed", failure_reason="M-PIN not set",
                )
                return Response(
                    {"detail": "M-PIN not set for this account. Please log in with your password."},
                    status=400,
                )
            if not user.mpin.check_mpin(mpin):
                record_login_activity(
                    request, user=user, status="failed", failure_reason="Incorrect M-PIN",
                )
                return Response({"detail": "Incorrect phone number or M-PIN."}, status=400)
        else:
            if not user.check_password(password):
                record_login_activity(
                    request, user=user, status="failed", failure_reason="Incorrect password",
                )
                return Response({"detail": "Incorrect phone number or password."}, status=400)

        if not user.is_active:
            record_login_activity(
                request, user=user, status="failed", failure_reason="Account deactivated or rejected",
            )
            return inactive_account_response(user)

        if not user.is_approved:
            record_login_activity(
                request, user=user, status="failed", failure_reason="Pending admin approval",
            )
            return Response({"detail": "pending_approval"}, status=403)

        # AMC-expiry gate — only customers have a company; staff/admin are
        # unaffected. See _customer_status (serializers.py) for the matching
        # "Expired" status shown to admin, which flips back to "Active" on
        # its own once the dates are renewed past today.
        company = getattr(user, "company", None)
        if company and company.amc_end_date and company.amc_end_date < timezone.localdate():
            record_login_activity(
                request, user=user, status="failed", failure_reason="AMC validity expired",
            )
            return Response(
                {"detail": "amc_expired",
                 "message": "Your AMC validity has expired. Please contact admin to renew."},
                status=403,
            )

        if not hasattr(user, "mpin"):
            return Response({"mpin_required": True})

        # Single-active-session enforcement — block a second device until the
        # first one explicitly logs out. The SAME device (matching device_id)
        # is always allowed back in; a stale session past the refresh token's
        # lifetime is treated as dead even if logout was never called (e.g.
        # a crashed browser), so the account can't be locked out forever.
        device_id = (data.get("device_id") or "").strip()
        if user.active_device_id and user.active_device_id != device_id:
            refresh_lifetime = settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME", timedelta(days=7))
            session_expired = (
                not user.active_login_at
                or timezone.now() - user.active_login_at > refresh_lifetime
            )
            if not session_expired:
                record_login_activity(
                    request, user=user, status="failed",
                    failure_reason="Already logged in on another device",
                )
                return Response(
                    {"detail": "already_logged_in",
                     "message": "This account is already signed in on another device. Please log out there first."},
                    status=409,
                )

        user.active_device_id = device_id
        user.active_login_at = timezone.now()
        user.save(update_fields=["active_device_id", "active_login_at"])

        record_login_activity(request, user=user, status="success")
        return Response(issue_tokens(user))


class SessionCheckView(APIView):
    """GET — a trivial authenticated ping. On its own it does nothing; the
    point is that DeviceCheckedJWTAuthentication runs first and 401s a
    superseded token. Polled periodically from MainLayout (App.jsx) so an
    idle tab that isn't otherwise making API calls still discovers within
    a few seconds that 'logout from all devices' (or a newer login
    elsewhere) ended its session, instead of only finding out the next
    time it happens to hit a real endpoint."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"ok": True})


class ForceLogoutView(APIView):
    """Public — lets someone blocked by the single-active-session gate (they
    got 'already_logged_in' on the login screen) clear that stale session
    without needing physical access to the other device. Re-proves account
    ownership with the same password/M-PIN check LoginView uses, then clears
    active_device_id/active_login_at. Does not issue tokens — the user still
    logs in normally afterward."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = User.objects.filter(phone_number=data["phone_number"]).first()
        if not user:
            return Response({"detail": "Incorrect phone number or credentials."}, status=400)

        mpin = data.get("mpin")
        if mpin:
            if not hasattr(user, "mpin") or not user.mpin.check_mpin(mpin):
                return Response({"detail": "Incorrect phone number or M-PIN."}, status=400)
        else:
            if not user.check_password(data.get("password")):
                return Response({"detail": "Incorrect phone number or password."}, status=400)

        user.active_device_id = ""
        user.active_login_at = None
        user.save(update_fields=["active_device_id", "active_login_at"])

        return Response({"success": True})


class CreateMpinView(APIView):
    """Re-verifies the password and creates the M-PIN (does not log the user in)."""
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

class MyIpView(APIView):
    """Public — returns the caller's IP address, shown on the login screen."""
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"ip": get_client_ip(request)})


class ReportPasswordView(APIView):
    """Staff/admin only — the password they need to unlock editing of a
    ticket report .xlsx exported from All Tickets. Customers aren't allowed
    here; their export uses their own phone number's last 4 digits instead,
    computed client-side (see AllTickets.jsx)."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request):
        row = ReportPasswordSettings.objects.first()
        password = row.password if row else ReportPasswordSettings._meta.get_field("password").default
        return Response({"password": password})


class CheckMobileAvailabilityView(APIView):
    """Public pre-check for whether a mobile number is already registered."""
    permission_classes = [AllowAny]

    def get(self, request):
        mobile_number = (request.query_params.get("mobile_number") or "").strip()
        exists = bool(mobile_number) and User.objects.filter(phone_number=mobile_number).exists()
        return Response({"exists": exists})


class DropdownOptionListView(APIView):
    """Public, unauthenticated, read-only lookup for the onboarding form's
    admin-editable dropdowns (see DropdownOption in models.py). Returns
    { category: [value, value, ...], ... }, active options only, in
    display_order — add/remove options from the Django admin, not here."""
    permission_classes = [AllowAny]

    def get(self, request):
        grouped = {}
        for opt in DropdownOption.objects.filter(is_active=True):
            grouped.setdefault(opt.category, []).append(opt.value)
        return Response(grouped)


# ---------------------------------------------------------------------------
# Onboarding — public draft/submit endpoints
# ---------------------------------------------------------------------------

class OnboardingDraftView(APIView):
    """Upserts a Company in DRAFT status. No account is created here."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CompanyDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        company = serializer.save_draft()
        return Response({"draft_token": company.draft_token}, status=200)


class OnboardingSubmitView(APIView):
    """Creates the (unapproved) CustomUser account, finalises the Company as PENDING."""
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

        # Left as None (not '') when blank so multiple blanks don't collide on the unique constraint.
        company.company_code = company.company_code or None
        company.status = Company.Status.PENDING
        company.submitted_at = timezone.now()

        user = User.objects.create_user(
            phone_number=company.mobile_number,
            password=password,
            full_name=company.contact_name,
            email=company.email,
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
    """Despite the name (kept for URL/frontend compat), this returns every
    registration regardless of status — pending, approved, and rejected —
    so Account Approvals can render all three as separate sections."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request):
        companies = Company.objects.exclude(status=Company.Status.DRAFT).order_by("-submitted_at")
        return Response(CompanyListSerializer(companies, many=True).data)


class CompanyDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def get(self, request, company_id):
        company = Company.objects.filter(id=company_id).first()
        if not company:
            return Response({"detail": "Not found."}, status=404)
        return Response(CompanyDetailSerializer(company).data)


class ApproveCompanyView(APIView):
    """Unlocks login for an already-registered customer's account. Works from
    PENDING or REJECTED — an admin can change their mind on a previously
    rejected registration and approve it after all, since nothing was
    deleted."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, company_id):
        company = Company.objects.filter(
            id=company_id, status__in=[Company.Status.PENDING, Company.Status.REJECTED]
        ).first()
        if not company:
            return Response({"detail": "Registration not found."}, status=404)
        if not company.user:
            return Response({"detail": "No linked account for this registration."}, status=400)

        company.user.is_approved = True
        company.user.is_active = True  # undo a prior rejection's deactivation, if any
        company.user.save()

        company.status = Company.Status.APPROVED
        company.reviewed_at = timezone.now()
        company.reviewed_by = request.user
        company.save()

        send_approval_email(company)

        return Response({"detail": "Company approved. The customer can now log in."})


class RevokeCompanyApprovalView(APIView):
    """Reverses an approval: sends the company back to PENDING and blocks login."""
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
    """Soft reject — nothing is deleted. The Company row stays (status flips
    to REJECTED, account login blocked) so an admin can revisit and approve
    it later; the customer is emailed the reason."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, company_id):
        company = Company.objects.filter(
            id=company_id, status__in=[Company.Status.PENDING, Company.Status.REJECTED]
        ).first()
        if not company:
            return Response({"detail": "Registration not found."}, status=404)

        serializer = CompanyRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")

        company.status = Company.Status.REJECTED
        company.reviewed_at = timezone.now()
        company.reviewed_by = request.user
        company.rejection_reason = reason
        company.save()

        if company.user:
            company.user.is_active = False
            company.user.save()

        send_rejection_email(company, reason)

        return Response({"detail": "Company rejected."})


class MyProductsView(APIView):
    """Products this customer's company had verified by an admin during Account Approvals."""
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
    """Flips a staff member's active status. Deactivating releases every
    ticket/offer currently in their hands back to other eligible staff;
    reactivating re-offers them anything still open they're eligible for
    again — see release_staff_tickets/restore_staff_eligibility. Their
    StaffAssignment/StaffProduct rows are never touched either way —
    get_eligible_staff_ids excludes inactive staff, so deactivation
    "removes" and reactivation "restores" their links automatically."""
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, staff_id):
        user = User.objects.filter(id=staff_id, role=User.Role.STAFF).first()
        if not user:
            return Response({"detail": "Staff member not found."}, status=404)

        with transaction.atomic():
            was_active = user.is_active
            user.is_active = not user.is_active
            user.save()

            if was_active and not user.is_active:
                release_staff_tickets(user)
            elif not was_active and user.is_active:
                restore_staff_eligibility(user)

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
    """Tickets currently assigned to this staff member."""
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
    """Admin/staff only. Lists all customer-role accounts with search + filter."""
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
    """View/edit/delete a customer account. Delete is blocked (409) if the customer has raised tickets."""
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

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        ticket_count = Ticket.objects.filter(raised_by=instance).count()
        if ticket_count:
            noun = "ticket" if ticket_count == 1 else "tickets"
            return Response(
                {"detail": f"{instance.full_name} has raised {ticket_count} {noun} and cannot be deleted."},
                status=http_status.HTTP_409_CONFLICT,
            )
        instance.delete()
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class CustomerDeactivateView(APIView):
    """Toggles is_active for a customer."""
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
    """Admin/staff adds a Product Master catalog entry to a customer's company, pre-verified."""
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

        # Staff already linked to this product (Product Master's "staff who
        # handle this product") get assigned to this customer for it
        # automatically — mirrors what Account Approvals' Step C does
        # manually, so a product added post-onboarding still routes
        # tickets correctly without an admin having to reassign staff.
        linked_staff_ids = StaffProduct.objects.filter(
            product_name=product_master.name
        ).values_list("staff_id", flat=True)
        existing_staff_ids = set(
            StaffAssignment.objects.filter(
                company=company, product_name=product_master.name, is_current=True
            ).values_list("staff_id", flat=True)
        )
        StaffAssignment.objects.bulk_create([
            StaffAssignment(
                company=company, staff_id=staff_id, product_name=product_master.name,
                assigned_by=request.user,
            )
            for staff_id in linked_staff_ids if staff_id not in existing_staff_ids
        ])

        return Response(ProductSerializer(product).data, status=http_status.HTTP_201_CREATED)


class CustomerRemoveProductView(APIView):
    """Removes a Product row; also drops it from products_in_use/product_verification if unused elsewhere."""
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

        # Frees up the single-active-session slot so this account can log in
        # on another device again.
        request.user.active_device_id = ""
        request.user.active_login_at = None
        request.user.save(update_fields=["active_device_id", "active_login_at"])

        return Response({"detail": "Logged out successfully."})
    
class ProfileView(APIView):
    """Get/edit the logged-in user's own basic profile details."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(ProfileSerializer(request.user).data)

    def patch(self, request):
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProfileSerializer(request.user).data)


class RequestMpinChangeOtpView(APIView):
    """Generates and emails a 4-digit OTP to change the M-PIN (10 min expiry)."""
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
    """Marks the latest matching OTP row as verified (doesn't change the M-PIN yet)."""
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
    """Requires a verified, unused, unexpired OTP row for this user."""
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


# Forgot M-PIN — unauthenticated flow from the login screen's "Forgot M-PIN?" link.

class RequestForgotMpinOtpView(APIView):
    """Used from the login screen before any session exists; identity proven via emailed OTP."""
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
            return inactive_account_response(user)
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

        # Masked so the login screen can show it without exposing the full address.
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
    """Requires a verified, unused, unexpired OTP row for this user."""
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