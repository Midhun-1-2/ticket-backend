from rest_framework.permissions import BasePermission


class IsAdminOrStaff(BasePermission):
    """Allows access only to authenticated users whose role is admin or staff."""

    message = "Only admin or staff accounts can perform this action."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "role", None) in ("admin", "staff")
        )


class IsAdmin(BasePermission):
    """Allows access only to authenticated users whose role is admin."""

    message = "Only admin accounts can perform this action."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "role", None) == "admin"
        )
    
