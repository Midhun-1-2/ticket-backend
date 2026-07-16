from rest_framework import serializers
from .models import (
    Category, Ticket, TicketAttachment, TicketAssignment, TicketAssignmentEvent,
    TicketStatusHistory, ProductMaster,
)


class CategorySerializer(serializers.ModelSerializer):
    # Whether this category is referenced by any ticket (locks the delete button).
    in_use = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'priority', 'is_active', 'in_use', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_in_use(self, obj):
        return obj.tickets.exists()

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Category name is required.")
        qs = Category.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A category with this name already exists.")
        return value


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

class TicketAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketAttachment
        fields = ['id', 'file', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_at']


class RaisedBySerializer(serializers.Serializer):
    """Minimal read-only view of the user who raised the ticket (also used for assigned_staff)."""
    phone_number = serializers.CharField()
    full_name = serializers.CharField()
    role = serializers.CharField()


def _customer_allowed_products(user):
    """Products a customer is allowed to raise tickets against (mirrors MyProductsView)."""
    company = getattr(user, 'company', None)
    if not company or company.status != 'approved':
        return []
    verification = company.product_verification or {}
    return [
        name for name in company.products_in_use
        if verification.get(name) == 'Verified'
    ]


class TicketSerializer(serializers.ModelSerializer):
    # Matches on category name (not id) since that's what the frontend sends.
    category = serializers.SlugRelatedField(
        slug_field='name',
        queryset=Category.objects.filter(is_active=True),
    )
    attachments = TicketAttachmentSerializer(many=True, read_only=True)
    raised_by = RaisedBySerializer(read_only=True)
    assigned_staff = RaisedBySerializer(read_only=True)
    # Latest status-change remark, visible to everyone (distinct from the admin-only full history).
    current_remark = serializers.SerializerMethodField()
    company_name = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            'id', 'subject', 'category', 'priority', 'description', 'product',
            'status', 'raised_by', 'assigned_staff', 'attachments',
            'escalated', 'escalated_at', 'escalation_note', 'closed_at',
            'current_remark', 'company_name', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'raised_by', 'assigned_staff',
            'escalated', 'escalated_at', 'escalation_note', 'closed_at',
            'current_remark', 'company_name', 'created_at', 'updated_at',
        ]

    def get_current_remark(self, obj):
        latest = obj.status_history.first()  # ordering = ['-created_at']
        return latest.remark if latest else ''

    def get_company_name(self, obj):
        company = getattr(getattr(obj.raised_by, 'company', None), 'company_name', None)
        return company or ''

    def validate_subject(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Subject is required.")
        return value

    def validate_product(self, value):
        # 'Not Applicable' is always allowed — the "no product" sentinel.
        if not value or value == 'Not Applicable':
            return value or 'Not Applicable'

        request = self.context.get('request')
        user = getattr(request, 'user', None)

        if user is not None and getattr(user, 'role', None) == 'customer':
            # Customers can only raise tickets for products verified on their account.
            allowed = _customer_allowed_products(user)
            if value not in allowed:
                raise serializers.ValidationError(
                    "You can only raise tickets for products verified on your account. "
                    "Contact support if you need a different product added."
                )
            return value

        # Staff/admin fall back to the full active Product Master catalog.
        if not ProductMaster.objects.filter(name=value, is_active=True).exists():
            raise serializers.ValidationError(
                "This product isn't in the current product catalog."
            )
        return value


class TicketStatusUpdateSerializer(serializers.Serializer):
    """POST/PATCH body for TicketStatusUpdateView — new status plus a compulsory remark."""
    STAFF_SETTABLE_STATUSES = ['In Progress', 'On Hold', 'Resolved', 'Closed']

    status = serializers.ChoiceField(choices=STAFF_SETTABLE_STATUSES)
    remark = serializers.CharField()

    def validate_remark(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A remark is required when changing the status.")
        return value


class TransferTicketSerializer(serializers.Serializer):
    """POST body for TransferTicketView — who to hand the ticket to."""
    staff_id = serializers.IntegerField()


class EscalateTicketSerializer(serializers.Serializer):
    """POST body for EscalateTicketView — an optional note for admin
    explaining why this needs attention."""
    reason = serializers.CharField(required=False, allow_blank=True, default='')


# ---------------------------------------------------------------------------
# Ticket Assignment — offer / accept / decline / transfer
# ---------------------------------------------------------------------------

class TicketAssignmentTicketSerializer(serializers.ModelSerializer):
    """Compact ticket summary for assignment rows (avoids nesting the full TicketSerializer)."""
    category = serializers.CharField(source='category.name', read_only=True)
    customer_name = serializers.CharField(source='raised_by.full_name', read_only=True, default='')
    company_name = serializers.SerializerMethodField()
    assigned_staff = RaisedBySerializer(read_only=True)

    class Meta:
        model = Ticket
        fields = [
            'id', 'subject', 'category', 'priority', 'product', 'status',
            'customer_name', 'company_name', 'escalated', 'assigned_staff', 'created_at',
        ]

    def get_company_name(self, obj):
        company = getattr(getattr(obj.raised_by, 'company', None), 'company_name', None)
        return company or ''


class StaffMiniSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    phone_number = serializers.CharField()
    role = serializers.CharField()


class TicketAssignmentSerializer(serializers.ModelSerializer):
    ticket = TicketAssignmentTicketSerializer(read_only=True)
    staff = StaffMiniSerializer(read_only=True)
    transferred_to = StaffMiniSerializer(read_only=True)

    class Meta:
        model = TicketAssignment
        fields = ['id', 'ticket', 'staff', 'status', 'offered_at', 'responded_at', 'transferred_to']
        read_only_fields = fields


class TicketAssignmentEventSerializer(serializers.ModelSerializer):
    """Full permanent audit trail row for a ticket assignment event."""
    staff = RaisedBySerializer(read_only=True)
    to_staff = RaisedBySerializer(read_only=True)

    class Meta:
        model = TicketAssignmentEvent
        fields = ['id', 'action', 'staff', 'to_staff', 'note', 'created_at']
        read_only_fields = fields


class TicketStatusHistorySerializer(serializers.ModelSerializer):
    """Admin-only full status-change trail for one ticket."""
    changed_by = RaisedBySerializer(read_only=True)

    class Meta:
        model = TicketStatusHistory
        fields = ['id', 'from_status', 'to_status', 'remark', 'changed_by', 'created_at']
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Product Master
# ---------------------------------------------------------------------------

def _product_in_use(product):
    """Whether a specific product (name + version) is purchased by any company."""
    from authentication.models import Product as CompanyProduct  # avoids circular import

    return CompanyProduct.objects.filter(
        product_name=product.name,
        product_version=product.version or '',
    ).exists()


class ProductMasterSerializer(serializers.ModelSerializer):
    # Lets the frontend lock the delete button — same pattern as CategorySerializer.in_use.
    in_use = serializers.SerializerMethodField()

    class Meta:
        model = ProductMaster
        fields = ['id', 'name', 'version', 'activation_date', 'is_active', 'in_use', 'created_at', 'updated_at']
        read_only_fields = ['id', 'in_use', 'created_at', 'updated_at']

    def get_in_use(self, obj):
        return _product_in_use(obj)

    def validate(self, attrs):
        # (name, version) must be unique together; falls back to instance values on PATCH.
        name = attrs.get('name', getattr(self.instance, 'name', None))
        version = attrs.get('version', getattr(self.instance, 'version', ''))
        if name:
            qs = ProductMaster.objects.filter(name__iexact=name, version__iexact=version or '')
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"version": "This exact product and version already exists."}
                )
        return attrs

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Product name is required.")
        return value


class PublicProductSerializer(serializers.ModelSerializer):
    """Minimal, public-safe shape used by customer registration to populate a product picker."""
    class Meta:
        model = ProductMaster
        fields = ['id', 'name', 'version']