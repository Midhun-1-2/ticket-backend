from rest_framework import serializers
from .models import Category, Ticket, TicketAttachment, TicketAssignment, TicketAssignmentEvent, ProductMaster


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


def _customer_allowed_products(user):
    """Products a customer is allowed to raise tickets against — mirrors
    authentication.views.MyProductsView exactly (same company-approved +
    per-product 'Verified' check), duplicated here rather than imported to
    avoid a ticketapp -> authentication.views circular import. If either
    file's logic changes, keep both in sync."""
    company = getattr(user, 'company', None)
    if not company or company.status != 'approved':
        return []
    verification = company.product_verification or {}
    return [
        name for name in company.products_in_use
        if verification.get(name) == 'Verified'
    ]


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
            'escalated', 'escalated_at', 'escalation_note', 'closed_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'raised_by', 'assigned_staff',
            'escalated', 'escalated_at', 'escalation_note', 'closed_at',
            'created_at', 'updated_at',
        ]

    def validate_subject(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Subject is required.")
        return value

    def validate_product(self, value):
        # 'Not Applicable' is always allowed — it isn't a real
        # ProductMaster row or a company product, just the "no product"
        # sentinel.
        if not value or value == 'Not Applicable':
            return value or 'Not Applicable'

        request = self.context.get('request')
        user = getattr(request, 'user', None)

        if user is not None and getattr(user, 'role', None) == 'customer':
            # Customers can only raise tickets against products their own
            # company has on file AND that an admin has verified — the
            # exact same list Raise Ticket's dropdown is built from (see
            # /my-products/). This is what actually stops "Projo" (or any
            # product not tied to the customer's account) from being
            # submitted, even if the request bypasses the frontend.
            allowed = _customer_allowed_products(user)
            if value not in allowed:
                raise serializers.ValidationError(
                    "You can only raise tickets for products verified on your account. "
                    "Contact support if you need a different product added."
                )
            return value

        # Staff/admin aren't tied to one company — fall back to the full
        # active Product Master catalog (they may be raising or editing a
        # ticket on a customer's behalf).
        if not ProductMaster.objects.filter(name=value, is_active=True).exists():
            raise serializers.ValidationError(
                "This product isn't in the current product catalog."
            )
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


class TicketAssignmentEventSerializer(serializers.ModelSerializer):
    """Full permanent audit trail row — see TicketAssignmentEvent's
    docstring in models.py for why this exists separately from
    TicketAssignmentSerializer above (that one only reflects CURRENT
    per-staff status; this one never overwrites anything)."""
    staff = RaisedBySerializer(read_only=True)
    to_staff = RaisedBySerializer(read_only=True)

    class Meta:
        model = TicketAssignmentEvent
        fields = ['id', 'action', 'staff', 'to_staff', 'note', 'created_at']
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Product Master
# ---------------------------------------------------------------------------

def _product_in_use(product):
    """A specific product ROW (name + version) is 'in use' if a company's
    onboarding record lists exactly that name+version as a purchased
    product. This is checked per-version, not per-name, so adding a new,
    unused version of an already-in-use product doesn't inherit the lock
    from its sibling versions — only the specific version a company
    actually has on file is protected.

    Ticket.product is deliberately NOT part of this check. Tickets only
    ever store a plain product NAME (see MyProductsView / Ticket.product),
    with no version field of their own — there's no way to know which
    version a given ticket was actually raised against. Including it here
    would make every version of a product permanently undeletable the
    moment any ticket exists for that name at all, which defeats the
    point of having separate, individually-removable versions.
    """
    from authentication.models import Product as CompanyProduct  # local import avoids circular import at module load time

    return CompanyProduct.objects.filter(
        product_name=product.name,
        product_version=product.version or '',
    ).exists()


class ProductMasterSerializer(serializers.ModelSerializer):
    # Lets the frontend lock the delete button instead of round-tripping
    # into a 409 — same pattern as CategorySerializer.in_use.
    in_use = serializers.SerializerMethodField()

    class Meta:
        model = ProductMaster
        fields = ['id', 'name', 'version', 'activation_date', 'is_active', 'in_use', 'created_at', 'updated_at']
        read_only_fields = ['id', 'in_use', 'created_at', 'updated_at']

    def get_in_use(self, obj):
        return _product_in_use(obj)

    def validate(self, attrs):
        # (name, version) must be unique together now, not name alone —
        # a product can have multiple versions, each its own row (see
        # ProductMaster.Meta.constraints). Falls back to instance values
        # for whichever field isn't present in a partial (PATCH) update.
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
    """Minimal, public-safe shape — used by customer registration
    (Onboarding.jsx) before the user has an account. Exposes only what's
    needed to populate a product picker."""
    class Meta:
        model = ProductMaster
        fields = ['id', 'name', 'version']