from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    Company, CompanyContactSettings, CompanyContactSettingsProxy, CustomUser,
    DropdownOptionProxy, LoginActivityProxy, Mpin, Product, ReportPasswordSettings,
    ReportPasswordSettingsProxy, StaffActivityProxy, StaffAssignment, StaffRole,
)


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
    """Admin view of which staff member is assigned to which company/product."""
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


@admin.register(DropdownOptionProxy)
class DropdownOptionAdmin(admin.ModelAdmin):
    """Add/remove options here for the onboarding form's Company Type, Industry
    Type, Annual Turnover, No. of Employees, AMC Status, Preferred Support
    Channel/Time, and Support Type dropdowns — no code change needed."""
    list_display = ("category", "value", "display_order", "is_active")
    list_filter = ("category", "is_active")
    list_editable = ("display_order", "is_active")
    search_fields = ("value",)
    ordering = ("category", "display_order", "id")


@admin.register(LoginActivityProxy)
class LoginActivityAdmin(admin.ModelAdmin):
    """Who logged in (or tried to), as what role, from which company, and
    from where/when — a read-only trail, not something to hand-edit."""
    list_display = (
        "full_name", "phone_number", "role", "company_name",
        "status_badge", "location", "ip_address", "created_at",
    )
    list_filter = ("status", "role", "created_at")
    search_fields = ("full_name", "phone_number", "company_name", "ip_address")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = (
        "user", "full_name", "phone_number", "role", "company_name",
        "status", "failure_reason", "ip_address", "location", "user_agent", "created_at",
    )

    def status_badge(self, obj):
        color = "#0F6E63" if obj.status == "success" else "#C4432E"
        label = obj.get_status_display()
        if obj.status == "failed" and obj.failure_reason:
            label = f"{label} ({obj.failure_reason})"
        return format_html('<span style="color: {}; font-weight: 600;">{}</span>', color, label)
    status_badge.short_description = "Status"

    def has_add_permission(self, request):
        return False


@admin.register(StaffActivityProxy)
class StaffActivityLogAdmin(admin.ModelAdmin):
    """What each staff member has done — ticket status changes, transfers,
    escalations, and accept/decline of offers. Read-only trail."""
    list_display = ("full_name", "phone_number", "action", "description", "ip_address", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("full_name", "phone_number", "description", "ip_address")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = ("staff", "full_name", "phone_number", "action", "description", "ip_address", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(CompanyContactSettingsProxy)
class CompanyContactSettingsAdmin(admin.ModelAdmin):
    """Single row — the contact footer (logo, name, email, phone) shown at
    the bottom of the registration-received and account-approved emails.
    The changelist redirects straight to that one row's edit form (or the
    add form the very first time) so it behaves like a settings page."""
    fields = ("logo", "company_name", "contact_email", "contact_phone", "updated_at")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return not CompanyContactSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # URL names are keyed by the proxy's own app_label ("emailcontact"),
        # not "authentication" — see CompanyContactSettingsProxy.Meta.
        existing = CompanyContactSettings.objects.first()
        if existing:
            url = reverse("admin:emailcontact_companycontactsettingsproxy_change", args=[existing.pk])
        else:
            url = reverse("admin:emailcontact_companycontactsettingsproxy_add")
        return redirect(url)


@admin.register(ReportPasswordSettingsProxy)
class ReportPasswordSettingsAdmin(admin.ModelAdmin):
    """Single row — the password staff/admin need to edit an exported ticket
    report .xlsx. Customers use their own phone number's last 4 digits
    instead, so this only governs staff/admin exports. The changelist
    redirects straight to that one row's edit form, like a settings page.
    URL names are keyed by the proxy's own app_label ("reportpassword"), not
    "authentication" — see ReportPasswordSettingsProxy.Meta."""
    fields = ("password", "updated_at")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return not ReportPasswordSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        existing = ReportPasswordSettings.objects.first()
        if existing:
            url = reverse("admin:reportpassword_reportpasswordsettingsproxy_change", args=[existing.pk])
        else:
            url = reverse("admin:reportpassword_reportpasswordsettingsproxy_add")
        return redirect(url)