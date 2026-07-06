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

    # Hardcoded to match the frontend's PRODUCTS list for now — swap for a
    # FK to a real Product/Module model if that ever becomes its own thing.
    PRODUCT_CHOICES = [
        ('Not Applicable', 'Not Applicable'),
        ('Ticket Desk Pro', 'Ticket Desk Pro'),
        ('API Gateway', 'API Gateway'),
        ('SSO Add-on', 'SSO Add-on'),
        ('Analytics Suite', 'Analytics Suite'),
        ('Mobile App', 'Mobile App'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.CharField(max_length=200)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name='tickets'
    )
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='Medium')
    description = models.TextField()
    product = models.CharField(
        max_length=30, choices=PRODUCT_CHOICES, default='Not Applicable', blank=True
    )
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
    # (see AcceptTicketAssignmentView). Null means no one has claimed it yet.
    assigned_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets_assigned_to',
    )

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
# Ticket Assignment — offer / accept / decline
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
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('unavailable', 'Unavailable'),  # lost the race to another staff member
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='assignments')
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ticket_assignments'
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    offered_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

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
    'Product' dropdown on Raise Ticket, eventually replacing the hardcoded
    PRODUCT_CHOICES on Ticket)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True)
    version = models.CharField(max_length=30, blank=True)
    activation_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Product"
        verbose_name_plural = "Products"

    def __str__(self):
        return f"{self.name} ({self.version})" if self.version else self.name