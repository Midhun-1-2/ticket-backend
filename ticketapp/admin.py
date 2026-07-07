from django.contrib import admin
from .models import Category, Ticket, TicketAttachment, TicketAssignment, ProductMaster


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'priority', 'is_active')
    list_filter = ('priority', 'is_active')
    search_fields = ('name',)


class TicketAttachmentInline(admin.TabularInline):
    """TicketAttachment has a ForeignKey to Ticket ONLY — this inline must
    never appear in any ModelAdmin.inlines except TicketAdmin below, or
    Django's admin.E202 check fails at startup."""
    model = TicketAttachment
    extra = 0


class TicketAssignmentInline(admin.TabularInline):
    """Shows every staff offer made for this ticket right on the Ticket
    page — who it was offered to, and whether they accepted/declined/lost
    the race/were transferred — without needing to jump to the separate
    TicketAssignment list."""
    model = TicketAssignment
    extra = 0
    fields = ("staff", "status", "transferred_to", "offered_at", "responded_at")
    readonly_fields = ("offered_at",)
    autocomplete_fields = ("staff", "transferred_to")


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        'subject', 'category', 'priority', 'status', 'raised_by',
        'assigned_staff', 'escalated', 'created_at',
    )
    list_filter = ('status', 'priority', 'category', 'escalated')
    search_fields = ('subject', 'description')
    autocomplete_fields = ('raised_by', 'assigned_staff', 'category')
    # Both inlines belong here (on the Ticket page) — TicketAttachment and
    # TicketAssignment both have a ForeignKey to Ticket, not to each other.
    inlines = [TicketAttachmentInline, TicketAssignmentInline]


@admin.register(TicketAssignment)
class TicketAssignmentAdmin(admin.ModelAdmin):
    """
    Standalone view of every offer ever made — useful for debugging the
    accept race (e.g. confirming only one row per ticket ends up
    'accepted' and the rest flip to 'unavailable') without digging through
    each ticket's inline one at a time.
    """
    list_display = ('ticket', 'staff', 'status', 'transferred_to', 'offered_at', 'responded_at')
    list_filter = ('status',)
    search_fields = (
        'ticket__subject', 'staff__full_name', 'staff__phone_number',
    )
    autocomplete_fields = ('ticket', 'staff', 'transferred_to')
    readonly_fields = ('offered_at',)
    # Deliberately no `inlines` here — TicketAttachment has no FK to
    # TicketAssignment, which is exactly the admin.E202 error this file
    # is fixing.


@admin.register(ProductMaster)
class ProductMasterAdmin(admin.ModelAdmin):
    list_display = ('name', 'version', 'activation_date', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'version')