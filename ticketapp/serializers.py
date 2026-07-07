from rest_framework import serializers
from .models import Category, Ticket, TicketAttachment, TicketAssignment, ProductMaster


class CategorySerializer(serializers.ModelSerializer):
    # Tells the frontend whether this category is referenced by any ticket,
    # so the delete button can be locked instead of letting the request
    # round-trip into a 409 (the FK is PROTECT, so a hard delete would fail
    # anyway if this were ever out of sync).
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
    """Minimal read-only view of the user who raised the ticket. Also
    reused for assigned_staff below since CustomUser has the same three
    fields for staff accounts."""
    phone_number = serializers.CharField()
    full_name = serializers.CharField()
    role = serializers.CharField()


class TicketSerializer(serializers.ModelSerializer):
    # Frontend's category dropdown sends the category NAME as the value
    # (see <option value={c.name}>), so this matches on name rather than id
    # — no frontend changes needed. Only active categories are selectable.
    category = serializers.SlugRelatedField(
        slug_field='name',
        queryset=Category.objects.filter(is_active=True),
    )
    attachments = TicketAttachmentSerializer(many=True, read_only=True)
    raised_by = RaisedBySerializer(read_only=True)
    assigned_staff = RaisedBySerializer(read_only=True)

    class Meta:
        model = Ticket
        fields = [
            'id', 'subject', 'category', 'priority', 'description', 'product',
            'status', 'raised_by', 'assigned_staff', 'attachments',
            'escalated', 'escalated_at', 'escalation_note',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'raised_by', 'assigned_staff',
            'escalated', 'escalated_at', 'escalation_note',
            'created_at', 'updated_at',
        ]

    def validate_subject(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Subject is required.")
        return value


class TicketStatusUpdateSerializer(serializers.Serializer):
    """POST/PATCH body for TicketStatusUpdateView — just the new status.
    'Open' is deliberately excluded: that's the system's initial state
    before anyone accepts it, not something a staff member should be able
    to set once they own the ticket."""
    STAFF_SETTABLE_STATUSES = ['In Progress', 'On Hold', 'Resolved', 'Closed']

    status = serializers.ChoiceField(choices=STAFF_SETTABLE_STATUSES)


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
    """Compact ticket summary for assignment rows — avoids nesting the full
    TicketSerializer (attachments, description, etc.) which this screen
    doesn't need."""
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


# ---------------------------------------------------------------------------
# Product Master
# ---------------------------------------------------------------------------

class ProductMasterSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductMaster
        fields = ['id', 'name', 'version', 'activation_date', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Product name is required.")
        qs = ProductMaster.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A product with this name already exists.")
        return value


class PublicProductSerializer(serializers.ModelSerializer):
    """Minimal, public-safe shape — used by customer registration
    (Onboarding.jsx) before the user has an account. Exposes only what's
    needed to populate a product picker."""
    class Meta:
        model = ProductMaster
        fields = ['id', 'name', 'version']