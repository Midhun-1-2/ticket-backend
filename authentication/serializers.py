from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Company, Product

User = get_user_model()


class RoleDetectSerializer(serializers.Serializer):
    """Used by the login screen to show 'Role detected: X' as soon as a
    full phone number is typed, before the password/M-PIN is submitted."""
    phone_number = serializers.CharField(max_length=10, min_length=10)

    def to_role_response(self):
        phone_number = self.validated_data["phone_number"]
        user = User.objects.filter(phone_number=phone_number).first()
        if not user:
            return {"exists": False, "role": None}
        return {"exists": True, "role": user.role}


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
    form and clicks 'Save as Draft'."""
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
    from the form spec."""
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

    def validate_mobile_number(self, value):
        if not value.isdigit() or len(value) != 10:
            raise serializers.ValidationError("Mobile number must be exactly 10 digits.")
        if User.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError(
                "An account with this mobile number already exists."
            )
        return value


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
        ]


class CompanyRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)