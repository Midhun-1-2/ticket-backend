from django.contrib import admin
from .models import Category, Ticket, TicketAttachment, TicketAssignment

from .models import Category, Ticket, TicketAttachment, ProductMaster


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'priority', 'is_active')
    list_filter = ('priority', 'is_active')
    search_fields = ('name',)


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0


class TicketAssignmentInline(admin.TabularInline):
    """Shows every staff offer made for this ticket right on the Ticket
    page — who it was offered to, and whether they accepted/declined/lost
    the race — without needing to jump to the separate TicketAssignment
    list."""
    model = TicketAssignment
    extra = 0
    fields = ("staff", "status", "offered_at", "responded_at")
    readonly_fields = ("offered_at",)
    autocomplete_fields = ("staff",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('subject', 'category', 'priority', 'status', 'raised_by', 'assigned_staff', 'created_at')
    list_filter = ('status', 'priority', 'category')
    search_fields = ('subject', 'description')
    autocomplete_fields = ('raised_by', 'assigned_staff', 'category')
    inlines = [TicketAttachmentInline, TicketAssignmentInline]


@admin.register(TicketAssignment)
class TicketAssignmentAdmin(admin.ModelAdmin):
    """
    Standalone view of every offer ever made — useful for debugging the
    accept race (e.g. confirming only one row per ticket ends up
    'accepted' and the rest flip to 'unavailable') without digging through
    each ticket's inline one at a time.
    """
    list_display = ('ticket', 'staff', 'status', 'offered_at', 'responded_at')
    list_filter = ('status',)
    search_fields = (
        'ticket__subject', 'staff__full_name', 'staff__phone_number',
    )
    autocomplete_fields = ('ticket', 'staff')
    readonly_fields = ('offered_at',)
    inlines = [TicketAttachmentInline]

admin.site.register(ProductMaster)
