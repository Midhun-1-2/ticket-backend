from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Company, CustomUser, Mpin, Product, StaffAssignment, StaffRole


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    ordering = ("phone_number",)
    list_display = ("phone_number", "full_name", "role", "is_approved", "is_active", "date_joined")
    list_filter = ("role", "is_approved", "is_active")
    search_fields = ("phone_number", "full_name")

    fieldsets = (
        (None, {"fields": ("phone_number", "password")}),
        ("Personal info", {"fields": ("full_name", "role", "department", "designation")}),
        ("Approval & status", {"fields": ("is_approved", "is_active", "is_staff", "is_superuser")}),
        ("Permissions", {"fields": ("groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("phone_number", "full_name", "role", "password1", "password2"),
        }),
    )


@admin.register(StaffRole)
class StaffRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(StaffAssignment)
class StaffAssignmentAdmin(admin.ModelAdmin):
    """
    Lets you see/debug which staff member is tied to which company (and
    whether it's a primary assignment or scoped to one product) without
    needing to query the DB directly — handy while wiring up
    TicketAssignment's offer_ticket_to_eligible_staff(), since that
    function reads straight from this table.
    """
    list_display = ("staff", "company", "product_name", "is_current", "assigned_at", "assigned_by")
    list_filter = ("is_current", "product_name")
    search_fields = ("staff__full_name", "staff__phone_number", "company__company_name")
    autocomplete_fields = ("staff", "company", "assigned_by")
    readonly_fields = ("assigned_at",)


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


admin.site.register(Mpin)