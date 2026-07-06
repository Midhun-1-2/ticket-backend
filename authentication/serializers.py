from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Company, Product
from .models import StaffRole


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


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            "id", "product_name", "product_version", "activation_date",
            "support_type", "remarks",
        ]


class CompanyDraftSerializer(serializers.ModelSerializer):
    """Every field is optional — used while the user is still filling the
    form and clicks 'Save as Draft'.

    NOTE: the frontend no longer exposes a 'Save as Draft' button, but this
    endpoint is left in place in case you want to bring drafts back later
    (e.g. auto-save). It's unused by the current Onboarding.jsx.
    """
    products = ProductSerializer(many=True, required=False)
    draft_token = serializers.UUIDField(required=False)

    class Meta:
        model = Company
        fields = [
            "draft_token",
            "company_name", "company_type", "gst_number", "pan_number", "website",
            "industry_type", "annual_turnover", "employee_count",
            "address_line1", "address_line2", "city", "state", "country", "pincode",
            "contact_name", "designation", "email", "mobile_number", "phone_number",
            "alternate_email",
            "amc_status", "amc_start_date", "amc_end_date", "preferred_channel",
            "preferred_time", "remarks", "products_in_use", "contract_ref_number",
            "products",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
            if isinstance(field, (serializers.CharField, serializers.EmailField)):
                field.allow_blank = True

    def save_draft(self):
        validated = dict(self.validated_data)
        products_data = validated.pop("products", None)
        draft_token = validated.pop("draft_token", None)

        company = None
        if draft_token:
            company = Company.objects.filter(
                draft_token=draft_token, status=Company.Status.DRAFT
            ).first()

        if company:
            for field, value in validated.items():
                setattr(company, field, value)
            company.save()
        else:
            company = Company.objects.create(status=Company.Status.DRAFT, **validated)

        if products_data is not None:
            company.products.all().delete()
            for product in products_data:
                Product.objects.create(company=company, **product)

        return company


class CompanySubmitSerializer(serializers.ModelSerializer):
    """Stricter version used on final submit — enforces the required fields
    from the form spec, plus password/confirm_password for account creation.
    """
    products = ProductSerializer(many=True, required=False)
    draft_token = serializers.UUIDField(required=False)

    company_name = serializers.CharField(max_length=200)
    company_type = serializers.ChoiceField(choices=Company.CompanyType.choices)
    address_line1 = serializers.CharField(max_length=255)
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=100)
    country = serializers.CharField(max_length=100)
    pincode = serializers.CharField(max_length=10)
    contact_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    mobile_number = serializers.CharField(max_length=10, min_length=10)
    amc_status = serializers.ChoiceField(choices=Company.AmcStatus.choices)
    products_in_use = serializers.ListField(child=serializers.CharField(), min_length=1)

    # Not model fields — used only to create the CustomUser account, then discarded.
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Company
        fields = [
            "draft_token",
            "company_name", "company_type", "gst_number", "pan_number", "website",
            "industry_type", "annual_turnover", "employee_count",
            "address_line1", "address_line2", "city", "state", "country", "pincode",
            "contact_name", "designation", "email", "mobile_number", "phone_number",
            "alternate_email",
            "amc_status", "amc_start_date", "amc_end_date", "preferred_channel",
            "preferred_time", "remarks", "products_in_use", "contract_ref_number",
            "products", "password", "confirm_password",
        ]

    def validate_mobile_number(self, value):
        if not value.isdigit() or len(value) != 10:
            raise serializers.ValidationError("Mobile number must be exactly 10 digits.")
        if User.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError(
                "An account with this mobile number already exists."
            )
        return value

    def validate_phone_number(self, value):
        if value and (not value.isdigit() or len(value) != 10):
            raise serializers.ValidationError("Phone number must be exactly 10 digits.")
        return value

    def validate(self, attrs):
        if attrs.get("password") != attrs.get("confirm_password"):
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs


class CompanyListSerializer(serializers.ModelSerializer):
    """Used in the admin 'pending approvals' list."""
    class Meta:
        model = Company
        fields = [
            "id", "company_code", "company_name", "company_type", "contact_name",
            "email", "mobile_number", "city", "state", "status", "submitted_at",
        ]


class CompanyDetailSerializer(serializers.ModelSerializer):
    products = ProductSerializer(many=True, read_only=True)
    staff_assignment = serializers.SerializerMethodField()  # <-- added

    class Meta:
        model = Company
        fields = [
            "id", "company_code", "status",
            "company_name", "company_type", "gst_number", "pan_number", "website",
            "industry_type", "annual_turnover", "employee_count",
            "address_line1", "address_line2", "city", "state", "country", "pincode",
            "contact_name", "designation", "email", "mobile_number", "phone_number",
            "alternate_email",
            "amc_status", "amc_start_date", "amc_end_date", "preferred_channel",
            "preferred_time", "remarks", "products_in_use", "contract_ref_number",
            "products", "submitted_at", "reviewed_at",
            "product_verification", "verification_note",
            "staff_assignment",  # <-- added
        ]

    def get_staff_assignment(self, obj):
        current = obj.staff_assignments.filter(is_current=True).select_related("staff")
        if not current.exists():
            return None

        primary_rows = current.filter(product_name="")
        if primary_rows.exists():
            return {
                "mode": "primary",
                "primary_staff_ids": [row.staff_id for row in primary_rows],
                "primary_staff": [
                    {"id": row.staff_id, "name": row.staff.full_name} for row in primary_rows
                ],
            }

        per_product = {}
        for row in current.exclude(product_name=""):
            per_product.setdefault(row.product_name, []).append(
                {"id": row.staff_id, "name": row.staff.full_name}
            )
        return {"mode": "per-product", "per_product": per_product}

class CompanyRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)

class ProductVerificationSerializer(serializers.Serializer):
    """Accepts { product_verification: {name: status}, verification_note }"""
    VALID_STATUSES = ["Verified", "Needs Clarification", "Not Found in Records"]

    product_verification = serializers.DictField(
        child=serializers.ChoiceField(choices=VALID_STATUSES), required=False
    )
    verification_note = serializers.CharField(required=False, allow_blank=True)

class StaffAssignmentSaveSerializer(serializers.Serializer):
    """Accepts either:
      { mode: "primary", primary_staff_ids: [5, 8] }
    or
      { mode: "per-product", per_product: {"Ticket Desk Pro": [5, 8], "Billing Suite": [3]} }
    Multiple staff can be assigned to the same target — no single "main" contact.
    """
    mode = serializers.ChoiceField(choices=["primary", "per-product"])
    primary_staff_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=False
    )
    per_product = serializers.DictField(
        child=serializers.ListField(child=serializers.IntegerField(), allow_empty=False),
        required=False,
    )

    def validate(self, attrs):
        if attrs["mode"] == "primary" and not attrs.get("primary_staff_ids"):
            raise serializers.ValidationError("primary_staff_ids is required for primary mode.")
        if attrs["mode"] == "per-product" and not attrs.get("per_product"):
            raise serializers.ValidationError("per_product is required for per-product mode.")
        return attrs

# ---------------------------------------------------------------------------
# Staff Management
# ---------------------------------------------------------------------------

class StaffRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffRole
        fields = ["id", "name"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Role name cannot be blank.")
        return value


class StaffListSerializer(serializers.ModelSerializer):
    """Read shape — matches the frontend's expected keys directly."""
    name = serializers.CharField(source="full_name")
    phone = serializers.CharField(source="phone_number")
    role = serializers.SerializerMethodField()
    ticketsAssigned = serializers.IntegerField(source="tickets_assigned")
    status = serializers.SerializerMethodField()
    assignedCustomers = serializers.SerializerMethodField()  # <-- added

    class Meta:
        model = User
        fields = [
            "id", "name", "email", "phone", "department", "role",
            "ticketsAssigned", "status", "assignedCustomers",  # <-- added
        ]

    def get_role(self, obj):
        return obj.designation.name if obj.designation else ""

    def get_status(self, obj):
        return "active" if obj.is_active else "inactive"

    def get_assignedCustomers(self, obj):
        # Counts distinct APPROVED companies currently assigned to this
        # staff member — either as primary (product_name="") or for any
        # specific product. distinct() avoids double-counting a company
        # that has this staff assigned across multiple products.
        from .models import Company  # local import avoids circular import issues
        return (
            obj.customer_assignments
            .filter(is_current=True, company__status=Company.Status.APPROVED)
            .values("company")
            .distinct()
            .count()
        )
    
class StaffAssignedCustomerSerializer(serializers.Serializer):
    """Used by the Staff Management detail slide-over — lists the
    companies currently assigned to a given staff member."""
    company_id = serializers.IntegerField(source="company.id")
    company_name = serializers.CharField(source="company.company_name")
    product_name = serializers.CharField()  # "" means primary / all products
    assigned_at = serializers.DateTimeField()


class StaffCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=10, min_length=10)
    department = serializers.CharField(max_length=50)
    role = serializers.CharField(max_length=100)
    password = serializers.CharField(min_length=4)

    def validate_phone(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("Phone number must be exactly 10 digits.")
        if User.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return value

    def validate_role(self, value):
        if not StaffRole.objects.filter(name=value).exists():
            raise serializers.ValidationError("Unknown role/designation. Add it first.")
        return value

    def create(self, validated_data):
        designation = StaffRole.objects.get(name=validated_data["role"])
        return User.objects.create_user(
            phone_number=validated_data["phone"],
            password=validated_data["password"],
            full_name=validated_data["name"],
            email=validated_data["email"],
            department=validated_data["department"],
            designation=designation,
            role=User.Role.STAFF,
            is_approved=True,
        )


class StaffUpdateSerializer(serializers.Serializer):
    """All fields optional — used with partial=True for PATCH."""
    name = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(required=False)
    phone = serializers.CharField(max_length=10, min_length=10, required=False)
    department = serializers.CharField(max_length=50, required=False)
    role = serializers.CharField(max_length=100, required=False)

    def validate_phone(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("Phone number must be exactly 10 digits.")
        if User.objects.filter(phone_number=value).exclude(id=self.instance.id).exists():
            raise serializers.ValidationError("Another account already uses this phone number.")
        return value

    def validate_role(self, value):
        if not StaffRole.objects.filter(name=value).exists():
            raise serializers.ValidationError("Unknown role/designation. Add it first.")
        return value

    def update(self, instance, validated_data):
        if "name" in validated_data:
            instance.full_name = validated_data["name"]
        if "email" in validated_data:
            instance.email = validated_data["email"]
        if "phone" in validated_data:
            instance.phone_number = validated_data["phone"]
        if "department" in validated_data:
            instance.department = validated_data["department"]
        if "role" in validated_data:
            instance.designation = StaffRole.objects.get(name=validated_data["role"])
        instance.save()
        return instance


# ---------------------------------------------------------------------------
# Customer Management (Admin only)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Customer Management (Admin only)
# ---------------------------------------------------------------------------

def _customer_status(user):
    """Single source of truth for the three-state status shown in the UI."""
    if not user.is_active:
        return "Blocked"
    if not user.is_approved:
        return "Pending Approval"
    return "Active"


def _customer_email(user):
    """The account's own email is often blank — onboarding stores the
    contact email on Company.email instead. Fall back to that so the UI
    always shows the email the customer actually entered."""
    if user.email:
        return user.email
    company = getattr(user, "company", None)
    if company and company.email:
        return company.email
    return ""


class CustomerCompanySerializer(serializers.ModelSerializer):
    """Full company snapshot — every field captured during onboarding —
    shown on the customer View modal."""
    products = ProductSerializer(many=True, read_only=True)

    class Meta:
        model = Company
        fields = [
            "id", "company_code", "company_name", "company_type",
            "gst_number", "pan_number", "website", "industry_type",
            "annual_turnover", "employee_count",
            "address_line1", "address_line2", "city", "state", "country", "pincode",
            "contact_name", "designation", "email", "mobile_number", "phone_number",
            "alternate_email",
            "amc_status", "amc_start_date", "amc_end_date",
            "preferred_channel", "preferred_time", "remarks",
            "products_in_use", "contract_ref_number", "products",
            "status", "submitted_at", "reviewed_at",
        ]


class CustomerListSerializer(serializers.ModelSerializer):
    """Row shape for the customer table."""
    name = serializers.CharField(source="full_name")
    phone = serializers.CharField(source="phone_number")
    email = serializers.SerializerMethodField()
    company = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "name", "email", "phone", "company", "status", "date_joined"]

    def get_email(self, obj):
        return _customer_email(obj)

    def get_company(self, obj):
        company = getattr(obj, "company", None)
        return company.company_name if company else "—"

    def get_status(self, obj):
        return _customer_status(obj)


class CustomerDetailSerializer(serializers.ModelSerializer):
    """Full shape for the View modal — includes every onboarding field via
    the nested company object."""
    name = serializers.CharField(source="full_name")
    phone = serializers.CharField(source="phone_number")
    email = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    company = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "name", "email", "phone", "company", "status",
            "is_active", "is_approved", "date_joined",
        ]

    def get_email(self, obj):
        return _customer_email(obj)

    def get_company(self, obj):
        company = getattr(obj, "company", None)
        return CustomerCompanySerializer(company).data if company else None

    def get_status(self, obj):
        return _customer_status(obj)


class CustomerUpdateSerializer(serializers.ModelSerializer):
    """Used for PATCH — the Edit action. Only identity fields are editable
    here; company details belong to the onboarding flow, not this screen."""
    class Meta:
        model = User
        fields = ["full_name", "email", "phone_number"]
        extra_kwargs = {
            "full_name": {"required": False},
            "email": {"required": False},
            "phone_number": {"required": False},
        }

    def validate_full_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name is required.")
        return value

    def validate_phone_number(self, value):
        if not value.isdigit() or len(value) != 10:
            raise serializers.ValidationError("Phone number must be exactly 10 digits.")
        qs = User.objects.filter(phone_number=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Another account already uses this phone number.")
        return value