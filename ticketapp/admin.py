from django.contrib import admin
from .models import Category, Ticket, TicketAttachment, TicketAssignment, ProductMaster


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'priority', 'is_active')
    list_filter = ('priority', 'is_active')
    search_fields = ('name',)


class TicketAttachmentInline(admin.TabularInline):
    """Inline for a ticket's attachments."""
    model = TicketAttachment
    extra = 0


class TicketAssignmentInline(admin.TabularInline):
    """Inline showing every staff offer made for this ticket."""
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
    inlines = [TicketAttachmentInline, TicketAssignmentInline]


@admin.register(TicketAssignment)
class TicketAssignmentAdmin(admin.ModelAdmin):
    """Standalone view of every ticket assignment offer ever made."""
    list_display = ('ticket', 'staff', 'status', 'transferred_to', 'offered_at', 'responded_at')
    list_filter = ('status',)
    search_fields = (
        'ticket__subject', 'staff__full_name', 'staff__phone_number',
    )
    autocomplete_fields = ('ticket', 'staff', 'transferred_to')
    readonly_fields = ('offered_at',)


@admin.register(ProductMaster)
class ProductMasterAdmin(admin.ModelAdmin):
    list_display = ('name', 'version', 'activation_date', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'version')