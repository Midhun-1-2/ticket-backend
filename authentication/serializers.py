from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class RoleDetectSerializer(serializers.Serializer):
    """Used by the login screen to show 'Role detected: X' as soon as a
    full phone number is typed, before the password/M-PIN is submitted."""
    phone_number = serializers.CharField(max_length=10, min_length=10)

    def to_role_response(self):
        phone_number = self.validated_data["phone_number"]
        user = User.objects.filter(phone_number=phone_number).first()
        if not user:
            return {"exists": False, "role": None, "has_mpin": False}
        return {
            "exists": True,
            "role": user.role,
            "has_mpin": hasattr(user, "mpin"),
        }


class LoginInputSerializer(serializers.Serializer):
    """Accepts either password or mpin (not both required) alongside phone_number."""
    phone_number = serializers.CharField(max_length=10, min_length=10)
    password = serializers.CharField(required=False, allow_blank=True)
    mpin = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("password") and not attrs.get("mpin"):
            raise serializers.ValidationError("Password or M-PIN is required.")
        return attrs


class MpinCreateSerializer(serializers.Serializer):
    """Used on first-time login to create the M-PIN. Password is
    re-verified server-side so this can't be used to hijack another
    account's M-PIN."""
    phone_number = serializers.CharField(max_length=10, min_length=10)
    password = serializers.CharField()
    mpin = serializers.CharField(min_length=4, max_length=6)
    confirm_mpin = serializers.CharField(min_length=4, max_length=6)

    def validate(self, attrs):
        if not attrs["mpin"].isdigit():
            raise serializers.ValidationError("M-PIN must be numeric.")
        if attrs["mpin"] != attrs["confirm_mpin"]:
            raise serializers.ValidationError("M-PINs do not match.")
        return attrs
    
