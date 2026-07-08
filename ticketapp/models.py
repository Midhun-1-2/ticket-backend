import uuid
from django.conf import settings
from django.db import models


class Category(models.Model):
    PRIORITY_CHOICES = [
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
        ('Urgent', 'Urgent'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='Medium')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

class Ticket(models.Model):
    PRIORITY_CHOICES = [
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
        ('Urgent', 'Urgent'),
    ]

    STATUS_CHOICES = [
        ('Open', 'Open'),
        ('In Progress', 'In Progress'),
        ('On Hold', 'On Hold'),
        ('Resolved', 'Resolved'),
        ('Closed', 'Closed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.CharField(max_length=200)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name='tickets'
    )
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='Medium')
    description = models.TextField()

    # No longer a fixed choices list — validated dynamically against
    # ProductMaster in TicketSerializer.validate_product instead, so new
    # products added via Product Master work immediately without a
    # migration. 'Not Applicable' remains a valid sentinel value even
    # though it isn't a real ProductMaster row (see validate_product).
    # max_length matches ProductMaster.name's length so nothing here gets
    # silently truncated.
    product = models.CharField(max_length=150, blank=True, default='Not Applicable')

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='Open')

    # Who raised it — set automatically from the authenticated request,
    # never accepted directly from client input (see perform_create).
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets_raised',
    )

    # Set only once a staff member accepts an offer via TicketAssignment
    # (see AcceptTicketAssignmentView), or via a transfer
    # (see TransferTicketView). Null means no one has claimed it yet.
    assigned_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets_assigned_to',
    )

    # Escalation — flagged by the assigned staff member for admin
    # attention. Doesn't reassign the ticket; it stays with the staff
    # member, this just surfaces it. See EscalateTicketView.
    escalated = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    escalation_note = models.TextField(blank=True)

    # Set the first time status flips to 'Closed' via TicketStatusUpdateView
    # / TicketDetailView.perform_update. Cleared if the ticket is ever
    # reopened (moved to any other status), so this always reflects the
    # *current* closure, not a historical one.
    closed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.subject} ({self.status})"


class TicketAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='ticket_attachments/%Y/%m/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.file.name


# ---------------------------------------------------------------------------
# Ticket Assignment — offer / accept / decline / transfer
# ---------------------------------------------------------------------------

class TicketAssignment(models.Model):
    """
    One row per (ticket, staff) offer. When a ticket is raised, we create a
    'pending' row for every staff member currently tied to the customer's
    company (via authentication.StaffAssignment — primary or per-product).
    Whoever accepts first wins the ticket; every other pending row for that
    ticket flips to 'unavailable' in the same transaction (see
    AcceptTicketAssignmentView), so there's no window where two staff can
    both successfully claim the same ticket.

    A 'transferred' row is created when a staff member hands the ticket to
    someone else directly (see TransferTicketView) — the outgoing staff's
    row flips to 'transferred', and a new 'accepted' row is created for the
    incoming staff member (no race, since it's a direct handoff rather than
    a fresh multi-staff offer).
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('unavailable', 'Unavailable'),  # lost the race to another staff member
        ('transferred', 'Transferred'),  # handed off to another staff member
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='assignments')
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ticket_assignments'
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    offered_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    # Who this ticket was transferred TO, only set on the outgoing staff
    # member's row when status='transferred'. Lets the "Past offers" list
    # show "Transferred to <name>" instead of just a bare status label.
    transferred_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ticket_assignments_received_via_transfer',
    )

    class Meta:
        ordering = ['-offered_at']
        constraints = [
            models.UniqueConstraint(fields=['ticket', 'staff'], name='unique_ticket_staff_offer')
        ]

    def __str__(self):
        return f"{self.ticket_id} -> {self.staff} ({self.status})"


class ProductMaster(models.Model):
    """Admin-managed product catalog — distinct from authentication.Product,
    which records what a specific company has purchased/activated. This is
    the master list of products the ticketing system knows about (feeds the
    'Product' dropdown on Raise Ticket and Onboarding). Ticket.product is
    validated against this table dynamically (see TicketSerializer) rather
    than a fixed choices list.

    `name` is no longer globally unique on its own — a single product can
    have multiple versions, each stored as its own row sharing the same
    name. (name, version) together must be unique instead, so the same
    product/version pair can't be entered twice, but "Ticket Desk Pro"
    v1.0 and v2.0 can coexist as separate rows. See ProductMasterPage.jsx's
    "Add New Version" action, which POSTs a new row rather than PATCHing
    an existing one."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    version = models.CharField(max_length=30, blank=True)
    activation_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'version']
        verbose_name = "Product"
        verbose_name_plural = "Products"
        constraints = [
            models.UniqueConstraint(fields=['name', 'version'], name='unique_product_name_version')
        ]

    def __str__(self):
        return f"{self.name} ({self.version})" if self.version else self.name