import logging
import os
import uuid
from io import BytesIO

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.files.base import ContentFile
from django.core.validators import RegexValidator
from django.conf import settings
from django.db import models
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

phone_validator = RegexValidator(
    regex=r"^\d{10}$",
    message="Phone number must be exactly 10 digits.",
)


# Staff job title/designation (separate from CustomUser.role, the account type).
class StaffRole(models.Model):
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staff_roles"
        ordering = ["name"]

    def __str__(self):
        return self.name


# Staff department names, used to validate CustomUser.department values.
class StaffDepartment(models.Model):
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staff_departments"
        ordering = ["name"]

    def __str__(self):
        return self.name


class CustomUserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone_number, password, **extra_fields):
        if not phone_number:
            raise ValueError("Phone number is required.")
        user = self.model(phone_number=phone_number, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", CustomUser.Role.CUSTOMER)
        # New signups (customer or staff) require admin approval by default.
        extra_fields.setdefault("is_approved", False)
        return self._create_user(phone_number, password, **extra_fields)

    def create_superuser(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", CustomUser.Role.ADMIN)
        extra_fields.setdefault("is_approved", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(phone_number, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        STAFF = "staff", "Staff"
        ADMIN = "admin", "Admin"

    phone_number = models.CharField(
        max_length=10, unique=True, validators=[phone_validator]
    )
    full_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CUSTOMER)

    # Staff-only field.
    department = models.CharField(max_length=50, blank=True)
    designation = models.ForeignKey(
        StaffRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_members",
    )
    # Deprecated/unused counter, kept to avoid a column-dropping migration.
    tickets_assigned = models.PositiveIntegerField(default=0)

    # Admin must approve new accounts before login is allowed.
    is_approved = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # Django admin site access — separate from Role.STAFF
    date_joined = models.DateTimeField(auto_now_add=True)

    # Single-active-session enforcement — a client-generated id (persisted in
    # the browser's localStorage) identifying the device currently signed in.
    # Set on successful login, cleared on explicit logout; see LoginView and
    # LogoutView. active_login_at lets a login from the SAME account recover
    # automatically if it's older than the refresh token's lifetime, so a
    # crashed browser / cleared storage can't lock the account out forever.
    active_device_id = models.CharField(max_length=100, blank=True, default="")
    active_login_at = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.phone_number} ({self.role})"


class Mpin(models.Model):
    """Quick-login PIN, separate from the account password."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mpin",
    )
    mpin_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mpins"

    def set_mpin(self, raw_mpin):
        self.mpin_hash = make_password(raw_mpin)
        self.save()

    def check_mpin(self, raw_mpin):
        return check_password(raw_mpin, self.mpin_hash)

    def __str__(self):
        return f"MPIN for {self.user}"


class EmailOTP(models.Model):
    """Short-lived OTP sent to a user's registered email, for M-PIN change or forgot-M-PIN flows."""

    PURPOSE_MPIN_CHANGE = "mpin_change"
    PURPOSE_MPIN_FORGOT = "mpin_forgot"
    PURPOSE_CHOICES = [
        (PURPOSE_MPIN_CHANGE, "M-PIN Change"),
        (PURPOSE_MPIN_FORGOT, "M-PIN Forgot/Reset"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_otps"
    )
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    otp_hash = models.CharField(max_length=128)
    is_verified = models.BooleanField(default=False)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "email_otps"
        ordering = ["-created_at"]

    def set_otp(self, raw_otp):
        self.otp_hash = make_password(raw_otp)

    def check_otp(self, raw_otp):
        return check_password(raw_otp, self.otp_hash)

    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"OTP({self.purpose}) for {self.user}"


# Onboarding (company signup) — Company & Product.
class Company(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    draft_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="company",
    )

    company_name = models.CharField(max_length=200, blank=True)
    company_code = models.CharField(max_length=30, unique=True, null=True, blank=True)
    # Options for this field come from DropdownOption (category="company_type"), admin-editable.
    company_type = models.CharField(max_length=30, blank=True)
    gst_number = models.CharField(max_length=15, blank=True)
    pan_number = models.CharField(max_length=10, blank=True)
    website = models.URLField(blank=True)
    industry_type = models.CharField(max_length=50, blank=True)
    annual_turnover = models.CharField(max_length=30, blank=True)
    employee_count = models.CharField(max_length=20, blank=True)

    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="India", blank=True)
    pincode = models.CharField(max_length=10, blank=True)

    contact_name = models.CharField(max_length=150, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    mobile_number = models.CharField(max_length=10, validators=[phone_validator], blank=True)
    phone_number = models.CharField(max_length=10, blank=True)
    alternate_email = models.EmailField(blank=True)

    # Options for this field come from DropdownOption (category="amc_status"), admin-editable.
    amc_status = models.CharField(max_length=20, blank=True)
    amc_start_date = models.DateField(null=True, blank=True)
    amc_end_date = models.DateField(null=True, blank=True)
    preferred_channel = models.CharField(max_length=30, blank=True)
    preferred_time = models.CharField(max_length=30, blank=True)
    remarks = models.TextField(max_length=250, blank=True)
    products_in_use = models.JSONField(default=list, blank=True)
    contract_ref_number = models.CharField(max_length=50, blank=True)

    # Account Approvals Step B (Product Verification) status, keyed by product name.
    product_verification = models.JSONField(default=dict, blank=True)
    verification_note = models.TextField(blank=True)

    # Reason an admin gave when rejecting this registration — kept (not
    # cleared) even after a later approval, as a historical trace.
    rejection_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_companies",
    )

    class Meta:
        db_table = "companies"
        verbose_name = "Company"
        verbose_name_plural = "Companies"

    def __str__(self):
        return self.company_name or f"Draft {self.draft_token}"


class Product(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="products")
    product_name = models.CharField(max_length=100)
    product_version = models.CharField(max_length=30, blank=True)
    activation_date = models.DateField(null=True, blank=True)
    # Options for this field come from DropdownOption (category="support_type"), admin-editable.
    support_type = models.CharField(max_length=10, default="AMC")
    remarks = models.TextField(blank=True)

    class Meta:
        db_table = "company_products"

    def __str__(self):
        return f"{self.product_name} ({self.company.company_name})"

# Staff assignment history — reassigning marks old rows is_current=False rather than deleting.

class StaffAssignment(models.Model):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="staff_assignments"
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_assignments"
    )
    # Empty = primary staff for the whole company; a product name = scoped to that product.
    product_name = models.CharField(max_length=100, blank=True)

    is_current = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_assignments_made",
    )

    class Meta:
        db_table = "staff_assignments"
        ordering = ["-assigned_at"]

    def __str__(self):
        target = self.product_name or "all products"
        return f"{self.staff} ← {self.company} ({target})"


class StaffProduct(models.Model):
    """Which products a staff member can handle, globally (independent of company)."""
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_products"
    )
    product_name = models.CharField(max_length=100)
    # Specific version handled; blank = all versions.
    version = models.CharField(max_length=30, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staff_products"
        unique_together = [("staff", "product_name", "version")]

    def __str__(self):
        return f"{self.staff} → {self.product_name} ({self.version or 'any version'})"


class DropdownOption(models.Model):
    """Admin-editable options for the onboarding form's simple metadata dropdowns
    (Company Type, Industry Type, Annual Turnover, No. of Employees, AMC Status,
    Preferred Support Channel/Time, Support Type) — add/remove entries here instead
    of hardcoding them in the frontend."""

    class Category(models.TextChoices):
        COMPANY_TYPE = "company_type", "Company Type"
        INDUSTRY_TYPE = "industry_type", "Industry Type"
        TURNOVER_RANGE = "turnover_range", "Annual Turnover"
        EMPLOYEE_RANGE = "employee_range", "No. of Employees"
        AMC_STATUS = "amc_status", "AMC Status"
        SUPPORT_CHANNEL = "support_channel", "Preferred Support Channel"
        SUPPORT_TIME = "support_time", "Preferred Support Time"
        SUPPORT_TYPE = "support_type", "Support Type"

    category = models.CharField(max_length=30, choices=Category.choices)
    value = models.CharField(max_length=100)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "dropdown_options"
        ordering = ["category", "display_order", "id"]
        unique_together = [("category", "value")]
        verbose_name = "Dropdown Option"
        verbose_name_plural = "Dropdown Options"

    def __str__(self):
        return f"{self.get_category_display()}: {self.value}"


class LoginActivity(models.Model):
    """One row per login attempt (success or failure) — lets an admin see
    who signed in, as what role, from which company (customers), and from
    where/when, without digging through server logs."""

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="login_activities",
    )
    # Snapshotted at login time so the log stays readable even if the
    # account is later renamed, deactivated, or deleted.
    full_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=10, blank=True)
    role = models.CharField(max_length=20, blank=True)
    company_name = models.CharField(max_length=200, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices)
    failure_reason = models.CharField(max_length=100, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    location = models.CharField(max_length=100, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "login_activity"
        ordering = ["-created_at"]
        verbose_name = "Login Activity"
        verbose_name_plural = "Login Activity"

    def __str__(self):
        who = self.full_name or self.phone_number or "Unknown"
        return f"{who} - {self.get_status_display()} @ {self.created_at:%d %b %Y %H:%M}"


class StaffActivityLog(models.Model):
    """Append-only record of operations a staff member performs — ticket
    status changes, transfers, escalations, and accept/decline of offers."""

    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="activity_logs",
    )
    full_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=10, blank=True)

    action = models.CharField(max_length=50)
    description = models.CharField(max_length=255)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staff_activity_log"
        ordering = ["-created_at"]
        verbose_name = "Staff Activity"
        verbose_name_plural = "Staff Activity"

    def __str__(self):
        who = self.full_name or self.phone_number or "Unknown"
        return f"{who} - {self.action} @ {self.created_at:%d %b %Y %H:%M}"


class CompanyContactSettings(models.Model):
    """Singleton — the contact footer shown at the bottom of the
    registration-received and account-approved emails (logo, company name,
    email, phone). Only one row is ever kept; see save()."""

    company_name = models.CharField(max_length=150, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    logo = models.ImageField(
        upload_to="company_contact/",
        blank=True,
        null=True,
        help_text=(
            "Shown at the bottom of the registration-received and account-approved "
            "emails. Best results: a square PNG or JPG at least 200×200px with a "
            "plain or transparent background. Anything larger is automatically "
            "resized to fit, so there's no need to pre-crop it."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "company_contact_settings"
        verbose_name = "Email Contact Details"
        verbose_name_plural = "Email Contact Details"

    def __str__(self):
        return self.company_name or "Email contact details"

    # Only one row is ever meaningful — every save collapses onto pk=1
    # instead of letting the admin accidentally create a second row.
    def save(self, *args, **kwargs):
        self.pk = 1
        if self.logo:
            self._resize_logo()
        super().save(*args, **kwargs)

    def _resize_logo(self):
        """Downscales the uploaded logo to fit within a 200x200 box (upscaling
        never happens) and normalizes it to PNG, so any photo an admin
        uploads renders well in the email regardless of its original size."""
        max_size = (200, 200)
        try:
            from PIL import Image
            self.logo.seek(0)
            image = Image.open(self.logo)
            image = image.convert("RGBA") if image.mode not in ("RGB", "RGBA") else image
            if image.width > max_size[0] or image.height > max_size[1]:
                image.thumbnail(max_size, Image.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            new_name = os.path.splitext(os.path.basename(self.logo.name))[0] + ".png"
            self.logo.save(new_name, ContentFile(buffer.read()), save=False)
        except Exception:
            # Worst case the original upload is kept as-is — never block saving.
            logger.exception("Failed to resize company contact logo")


class ReportPasswordSettings(models.Model):
    """Singleton — the password required to edit the .xlsx ticket report
    exported from All Tickets by staff/admin. Customers get a different,
    per-customer default (last 4 digits of their own phone number) instead,
    computed on the frontend — this table only governs staff/admin exports.
    Only one row is ever kept; see save()."""

    password = models.CharField(max_length=50, default="tixa@1234")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "report_password_settings"
        verbose_name = "Report Password"
        verbose_name_plural = "Report Password"

    def __str__(self):
        return "Report password"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Admin-grouping proxies — same tables as above, just filed under their own
# "Activity Logs" / "Dropdown Options" / "Email Contact Details" sections in
# the Django admin instead of "Authentication" (see activitylog/
# dropdownoptions/emailcontact apps + admin.py).
# ---------------------------------------------------------------------------

class LoginActivityProxy(LoginActivity):
    class Meta:
        proxy = True
        app_label = "activitylog"
        verbose_name = "Login Activity"
        verbose_name_plural = "Login Activity"


class StaffActivityProxy(StaffActivityLog):
    class Meta:
        proxy = True
        app_label = "activitylog"
        verbose_name = "Staff Activity"
        verbose_name_plural = "Staff Activity"


class DropdownOptionProxy(DropdownOption):
    class Meta:
        proxy = True
        app_label = "dropdownoptions"
        verbose_name = "Dropdown Option"
        verbose_name_plural = "Dropdown Options"


class CompanyContactSettingsProxy(CompanyContactSettings):
    class Meta:
        proxy = True
        app_label = "emailcontact"
        verbose_name = "Email Contact Details"
        verbose_name_plural = "Email Contact Details"


class ReportPasswordSettingsProxy(ReportPasswordSettings):
    class Meta:
        proxy = True
        app_label = "reportpassword"
        verbose_name = "Report Password"
        verbose_name_plural = "Report Password"