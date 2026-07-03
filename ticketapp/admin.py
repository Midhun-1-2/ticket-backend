from django.contrib import admin

from .models import Category, Ticket, TicketAttachment

admin.site.register(Category)


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('subject', 'category', 'priority', 'status', 'raised_by', 'created_at')
    list_filter = ('status', 'priority', 'category')
    search_fields = ('subject', 'description')
    inlines = [TicketAttachmentInline]