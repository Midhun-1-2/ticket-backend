from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class DeviceCheckedJWTAuthentication(JWTAuthentication):
    """Rejects a token whose embedded device_id no longer matches the
    account's current active_device_id — this is what makes "log out from
    all devices" (ForceLogoutView) and a newer login elsewhere (LoginView's
    single-active-session gate) actually end the OTHER session, instead of
    just blocking new logins while the old device's still-valid token keeps
    working for up to its full lifetime. issue_tokens() is the only place
    tokens are minted, and it always stamps this claim, so a token without
    one (there shouldn't be any) is left alone rather than rejected.

    Also rejects a customer whose AMC expired since they logged in — same
    mechanism LoginView uses to block a fresh login, applied here so an
    already-open session gets cut short too, picked up by the frontend's
    periodic session-check poll (see App.jsx) rather than lingering until
    the access token's natural expiry."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        token_device_id = validated_token.get("device_id")
        if token_device_id is not None and token_device_id != (user.active_device_id or ""):
            raise AuthenticationFailed(
                "This session has been signed out.", code="session_superseded"
            )
        company = getattr(user, "company", None)
        if company and company.amc_end_date and company.amc_end_date < timezone.localdate():
            raise AuthenticationFailed(
                "Your AMC validity has expired. Please contact admin to renew.", code="amc_expired"
            )
        return user
