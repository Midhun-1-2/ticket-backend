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

    # Validated dynamically against ProductMaster (see TicketSerializer.validate_product).
    product = models.CharField(max_length=150, blank=True, default='Not Applicable')

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='Open')

    # Set automatically from the authenticated request (see perform_create).
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets_raised',
    )

    # Set once a staff member accepts or is transferred the ticket; null = unclaimed.
    assigned_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets_assigned_to',
    )

    # Flagged by assigned staff for admin attention; doesn't reassign the ticket.
    escalated = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    escalation_note = models.TextField(blank=True)

    # Set when status flips to 'Closed'; cleared if reopened.
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


# Ticket Assignment — offer / accept / decline / transfer.

class TicketAssignment(models.Model):
    """One row per (ticket, staff) offer; holds only the current status per pair (not a history log)."""
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

    # Who this ticket was transferred to; set on the outgoing staff member's row.
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


class TicketAssignmentEvent(models.Model):
    """Append-only, permanent audit trail of every assignment-related action on a ticket."""
    ACTION_CHOICES = [
        ('offered', 'Offered'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('unavailable', 'Unavailable'),
        ('transferred', 'Transferred'),
        ('escalated', 'Escalated'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='assignment_events')
    action = models.CharField(max_length=15, choices=ACTION_CHOICES)

    # Who this event is primarily about (offered/accepted/declined/outgoing staff).
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ticket_assignment_events',
    )
    # Only set for 'transferred'/'escalated' — who it was handed to.
    to_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ticket_assignment_events_received',
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.ticket_id}: {self.action} ({self.staff})"


class TicketStatusHistory(models.Model):
    """Permanent, append-only log of every status change on a ticket, with the remark given."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='status_history')
    from_status = models.CharField(max_length=15, choices=Ticket.STATUS_CHOICES)
    to_status = models.CharField(max_length=15, choices=Ticket.STATUS_CHOICES)
    remark = models.TextField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ticket_status_changes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Ticket status histories"

    def __str__(self):
        return f"{self.ticket_id}: {self.from_status} -> {self.to_status}"


class ProductMaster(models.Model):
    """Admin-managed product catalog; (name, version) pairs must be unique."""

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