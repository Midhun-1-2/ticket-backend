from django.contrib.auth import get_user_model
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Mpin
from .serializers import LoginInputSerializer, MpinCreateSerializer, RoleDetectSerializer

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
    Re-verifies the password, creates the M-PIN, and logs the user in."""
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

        return Response(issue_tokens(user))


class DetectRoleView(APIView):
    """POST { phone_number } -> { exists, role }"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RoleDetectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.to_role_response())