from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

<<<<<<< HEAD
from .models import Company, CustomUser, Product
=======
from .models import CustomUser,Mpin
>>>>>>> 708ed513a7026fe5d08ec211db8596e7a52df956


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

<<<<<<< HEAD

class ProductInline(admin.TabularInline):
    model = Product
    extra = 0


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "company_code", "company_name", "contact_name", "mobile_number",
        "status", "submitted_at",
    )
    list_filter = ("status", "company_type", "amc_status")
    search_fields = ("company_name", "company_code", "contact_name", "mobile_number", "email")
    inlines = [ProductInline]
    readonly_fields = ("draft_token", "company_code", "created_at", "updated_at")
=======
admin.site.register(Mpin)
>>>>>>> 708ed513a7026fe5d08ec211db8596e7a52df956
