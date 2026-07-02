from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Company, Mpin
from .permissions import IsAdminOrStaff
from .serializers import (
    CompanyDetailSerializer,
    CompanyDraftSerializer,
    CompanyListSerializer,
    CompanyRejectSerializer,
    CompanySubmitSerializer,
    LoginInputSerializer,
    MpinCreateSerializer,
    RoleDetectSerializer,
)
from .utils import (
    generate_company_code,
    generate_temp_password,
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
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CompanyDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        company = serializer.save_draft()
        return Response({"draft_token": company.draft_token}, status=200)


class OnboardingSubmitView(APIView):
    """
    POST { draft_token?, ...all form fields, products: [...] }
    Validates required fields, creates the (unapproved) CustomUser account,
    finalises the Company as PENDING, and emails a "received" confirmation.
    """
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        serializer = CompanySubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = dict(serializer.validated_data)

        products_data = validated.pop("products", [])
        draft_token = validated.pop("draft_token", None)

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

        # Create the linked (unapproved) account. Login uses mobile number,
        # matching the existing auth system's USERNAME_FIELD.
        user = User.objects.create_user(
            phone_number=company.mobile_number,
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
    permission_classes = [IsAuthenticated, IsAdminOrStaff]

    def post(self, request, company_id):
        company = Company.objects.filter(id=company_id, status=Company.Status.PENDING).first()
        if not company:
            return Response({"detail": "Pending registration not found."}, status=404)
        if not company.user:
            return Response({"detail": "No linked account for this registration."}, status=400)

        temp_password = generate_temp_password()
        company.user.set_password(temp_password)
        company.user.is_approved = True
        company.user.save()

        company.status = Company.Status.APPROVED
        company.reviewed_at = timezone.now()
        company.reviewed_by = request.user
        company.save()

        send_approval_email(company, temp_password)

        return Response({"detail": "Company approved. Credentials emailed to customer."})


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