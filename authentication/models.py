import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.validators import RegexValidator
from django.conf import settings
from django.db import models

phone_validator = RegexValidator(
    regex=r"^\d{10}$",
    message="Phone number must be exactly 10 digits.",
)


# ---------------------------------------------------------------------------
# Staff roles / designations (e.g. "Support Agent", "Team Lead") — separate
# from CustomUser.role (customer/staff/admin, the account TYPE). This is the
# job title shown in the Staff Management UI's "Role" column and the
# "Add Role/Designation" modal.
# ---------------------------------------------------------------------------

class StaffRole(models.Model):
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staff_roles"
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

    # Populated for Role.STAFF accounts only (created via Staff Management).
    department = models.CharField(max_length=50, blank=True)
    designation = models.ForeignKey(
        StaffRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_members",
    )
    # Simple counter for now — swap to a computed value (e.g. Ticket.objects
    # .filter(assigned_to=user).count()) once a Ticket model exists.
    tickets_assigned = models.PositiveIntegerField(default=0)

    # Approval workflow (Admin approves new accounts before login is allowed)
    is_approved = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # Django admin site access — separate from Role.STAFF
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = CustomUserManager()

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.phone_number} ({self.role})"


class Mpin(models.Model):
    """Separate from the account password. Created the first time a user
    logs in with their password; used as a quick-login alternative
    afterward."""
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


# ---------------------------------------------------------------------------
# Onboarding (company signup) — Company & Product
# ---------------------------------------------------------------------------

class Company(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    class CompanyType(models.TextChoices):
        PRIVATE_LIMITED = "Private Limited", "Private Limited"
        PUBLIC_LIMITED = "Public Limited", "Public Limited"
        LLP = "LLP", "LLP"
        PARTNERSHIP = "Partnership", "Partnership"
        SOLE_PROPRIETORSHIP = "Sole Proprietorship", "Sole Proprietorship"
        GOVERNMENT = "Government", "Government"
        NON_PROFIT = "Non-Profit", "Non-Profit"

    class AmcStatus(models.TextChoices):
        ACTIVE = "Active", "Active"
        INACTIVE = "Inactive", "Inactive"
        EXPIRED = "Expired", "Expired"
        NOT_APPLICABLE = "Not Applicable", "Not Applicable"

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
    company_type = models.CharField(max_length=30, choices=CompanyType.choices, blank=True)
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

    amc_status = models.CharField(max_length=20, choices=AmcStatus.choices, blank=True)
    amc_start_date = models.DateField(null=True, blank=True)
    amc_end_date = models.DateField(null=True, blank=True)
    preferred_channel = models.CharField(max_length=30, blank=True)
    preferred_time = models.CharField(max_length=30, blank=True)
    remarks = models.TextField(max_length=250, blank=True)
    products_in_use = models.JSONField(default=list, blank=True)
    contract_ref_number = models.CharField(max_length=50, blank=True)

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

    def __str__(self):
        return self.company_name or f"Draft {self.draft_token}"


class Product(models.Model):
    class SupportType(models.TextChoices):
        AMC = "AMC", "AMC"
        NON_AMC = "NON-AMC", "NON-AMC"
        SAS = "SAS", "SAS"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="products")
    product_name = models.CharField(max_length=100)
    product_version = models.CharField(max_length=30, blank=True)
    activation_date = models.DateField(null=True, blank=True)
    support_type = models.CharField(max_length=10, choices=SupportType.choices, default=SupportType.AMC)
    remarks = models.TextField(blank=True)

    class Meta:
        db_table = "company_products"

    def __str__(self):
        return f"{self.product_name} ({self.company.company_name})"