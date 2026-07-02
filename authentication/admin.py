from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CustomUser,Mpin


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    ordering = ("phone_number",)
    list_display = ("phone_number", "full_name", "role", "is_approved", "is_active", "date_joined")
    list_filter = ("role", "is_approved", "is_active")
    search_fields = ("phone_number", "full_name")

    fieldsets = (
        (None, {"fields": ("phone_number", "password")}),
        ("Personal info", {"fields": ("full_name", "role")}),
        ("Approval & status", {"fields": ("is_approved", "is_active", "is_staff", "is_superuser")}),
        ("Permissions", {"fields": ("groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("phone_number", "full_name", "role", "password1", "password2"),
        }),
    )

admin.site.register(Mpin)
