from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import StaffAssignment, StaffProduct
from authentication.permissions import IsAdmin, IsAdminOrStaff
from authentication.email_templates import (
    build_ticket_raised_email_html,
    build_ticket_raised_email_text,
    build_ticket_resolved_email_html,
    build_ticket_resolved_email_text,
    send_branded_email,
)

from .models import (
    Category, Ticket, TicketAssignment, TicketAssignmentEvent,
    TicketStatusHistory, ProductMaster,
)
from .serializers import (
    CategorySerializer,
    TicketSerializer,
    TicketStatusUpdateSerializer,
    TransferTicketSerializer,
    EscalateTicketSerializer,
    TicketAssignmentSerializer,
    TicketAssignmentEventSerializer,
    TicketStatusHistorySerializer,
    ProductMasterSerializer,
    PublicProductSerializer,
)

User = get_user_model()


def log_assignment_event(ticket, action, staff=None, to_staff=None, note=''):
    """Appends one permanent row to TicketAssignmentEvent."""
    TicketAssignmentEvent.objects.create(
        ticket=ticket, action=action, staff=staff, to_staff=to_staff, note=note,
    )


def send_ticket_raised_email(ticket):
    """Confirmation email to the customer right after they raise a ticket. Fails silently."""
    customer = ticket.raised_by
    if not customer or not customer.email:
        return
    try:
        text_body = build_ticket_raised_email_text(
            customer.full_name or customer.phone_number,
            ticket.id, ticket.subject, ticket.category.name, ticket.priority,
            ticket.product, ticket.description,
        )
        html_body = build_ticket_raised_email_html(
            customer.full_name or customer.phone_number,
            ticket.id, ticket.subject, ticket.category.name, ticket.priority,
            ticket.product, ticket.description,
        )
        send_branded_email(customer.email, "We've got your ticket", text_body, html_body)
    except Exception:
        # Ticket creation must succeed even if the mail server is down.
        pass


def send_ticket_resolved_email(ticket, resolved_by):
    """Notification email to the customer when their ticket moves to Resolved."""
    customer = ticket.raised_by
    if not customer or not customer.email:
        return
    try:
        resolved_by_name = getattr(resolved_by, 'full_name', '') or None
        text_body = build_ticket_resolved_email_text(
            customer.full_name or customer.phone_number,
            ticket.id, ticket.subject, resolved_by_name,
        )
        html_body = build_ticket_resolved_email_html(
            customer.full_name or customer.phone_number,
            ticket.id, ticket.subject, resolved_by_name,
        )
        send_branded_email(customer.email, "Your ticket has been resolved", text_body, html_body)
    except Exception:
        pass


class CategoryListCreateView(generics.ListCreateAPIView):
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Category.objects.all()
        if self.request.query_params.get('include_inactive') != 'true':
            qs = qs.filter(is_active=True)
        return qs


class CategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Category.objects.all()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        # Categories referenced by any ticket can't be removed.
        if instance.tickets.exists():
            return Response(
                {"detail": "This category is in use by existing tickets and cannot be deleted."},
                status=409,
            )
        instance.delete()
        return Response(status=204)


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

def get_eligible_staff_ids(ticket):
    """Staff currently tied to the ticket's raising company, primary or scoped to its product."""
    raiser = ticket.raised_by
    company = getattr(raiser, 'company', None) if raiser else None
    if not company:
        return set()

    company_staff_ids = set(
        StaffAssignment.objects.filter(company=company, is_current=True)
        .filter(Q(product_name='') | Q(product_name=ticket.product))
        .values_list('staff_id', flat=True)
    )

    # Narrow to staff configured (via Product Master) to handle this specific product.
    product_staff_ids = set(
        StaffProduct.objects.filter(product_name=ticket.product).values_list('staff_id', flat=True)
    )
    if product_staff_ids:
        return company_staff_ids & product_staff_ids
    return company_staff_ids


def offer_ticket_to_eligible_staff(ticket):
    """Creates a 'pending' TicketAssignment for every eligible staff member, called on ticket creation/reopen."""
    staff_ids = get_eligible_staff_ids(ticket)

    touched = []
    for staff_id in staff_ids:
        obj, was_created = TicketAssignment.objects.get_or_create(
            ticket=ticket, staff_id=staff_id, defaults={'status': 'pending'}
        )
        if not was_created and obj.status != 'pending':
            obj.status = 'pending'
            obj.responded_at = None
            obj.transferred_to = None
            obj.save(update_fields=['status', 'responded_at', 'transferred_to'])
        touched.append(obj)
        log_assignment_event(ticket, 'offered', staff=obj.staff)
    return touched


class TicketListCreateView(generics.ListCreateAPIView):
    """List/create tickets — scoped to own tickets for customers/staff, all for admin."""
    serializer_class = TicketSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Ticket.objects.select_related(
            'category', 'raised_by', 'assigned_staff'
        ).prefetch_related('attachments', 'status_history')
        role = getattr(user, 'role', None)
        if role == 'customer':
            qs = qs.filter(raised_by=user)
        elif role == 'staff':
            qs = qs.filter(assigned_staff=user)
        return qs

    def perform_create(self, serializer):
        ticket = serializer.save(raised_by=self.request.user)
        for f in self.request.FILES.getlist('attachments'):
            ticket.attachments.create(file=f)
        offer_ticket_to_eligible_staff(ticket)
        send_ticket_raised_email(ticket)


class TicketDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve/update/delete a ticket. Customers can only access their own."""
    serializer_class = TicketSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Ticket.objects.select_related(
            'category', 'raised_by', 'assigned_staff'
        ).prefetch_related('attachments', 'status_history')
        if getattr(user, 'role', None) == 'customer':
            qs = qs.filter(raised_by=user)
        return qs


def _can_manage_ticket(user, ticket):
    """Who can change a ticket's status, transfer it, or escalate it."""
    if ticket.assigned_staff_id is None:
        return getattr(user, 'role', None) == 'admin'
    if getattr(ticket.assigned_staff, 'role', None) == 'admin':
        return getattr(user, 'role', None) == 'admin'
    return ticket.assigned_staff_id == user.id


class TicketEligibleStaffView(APIView):
    """Staff ids tied to this ticket's company, used to group the Transfer picker."""
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def get(self, request, pk):
        ticket = Ticket.objects.filter(id=pk).first()
        if not ticket:
            return Response({'detail': 'Ticket not found.'}, status=404)
        return Response({'staff_ids': list(get_eligible_staff_ids(ticket))})


class TicketAssignmentHistoryView(generics.ListAPIView):
    """Full, permanent, chronological audit trail for one ticket's assignment journey."""
    serializer_class = TicketAssignmentEventSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def get_queryset(self):
        return TicketAssignmentEvent.objects.filter(
            ticket_id=self.kwargs['pk']
        ).select_related('staff', 'to_staff')


class TicketStatusHistoryView(generics.ListAPIView):
    """Full, permanent, chronological trail of every status change on this ticket. Admin-only."""
    serializer_class = TicketStatusHistorySerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return TicketStatusHistory.objects.filter(
            ticket_id=self.kwargs['pk']
        ).select_related('changed_by')


class TicketStatusUpdateView(APIView):
    """Lets the assigned staff member (or admin) move a ticket through its status flow."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        ticket = Ticket.objects.filter(id=pk).first()
        if not ticket:
            return Response({'detail': 'Ticket not found.'}, status=404)
        if not _can_manage_ticket(request.user, ticket):
            return Response({'detail': 'Only the assigned staff member or an admin can update this ticket.'}, status=403)

        serializer = TicketStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        previous_status = ticket.status
        new_status = serializer.validated_data['status']
        remark = serializer.validated_data['remark']

        ticket.status = new_status
        ticket.save(update_fields=['status', 'updated_at'])

        TicketStatusHistory.objects.create(
            ticket=ticket, from_status=previous_status, to_status=new_status,
            remark=remark, changed_by=request.user,
        )

        # Only fire on the transition INTO Resolved.
        if ticket.status == 'Resolved' and previous_status != 'Resolved':
            send_ticket_resolved_email(ticket, request.user)

        return Response(TicketSerializer(ticket).data)


class TransferTicketView(APIView):
    """Hands a ticket to another staff member as a fresh PENDING offer they must accept/decline."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        ticket = Ticket.objects.filter(id=pk).first()
        if not ticket:
            return Response({'detail': 'Ticket not found.'}, status=404)
        if not _can_manage_ticket(request.user, ticket):
            return Response({'detail': 'Only the assigned staff member or an admin can transfer this ticket.'}, status=403)

        serializer = TransferTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_staff_id = serializer.validated_data['staff_id']

        new_staff = User.objects.filter(id=new_staff_id, role=User.Role.STAFF).first()
        if not new_staff:
            return Response({'detail': 'Selected staff member not found.'}, status=404)
        if ticket.assigned_staff_id == new_staff.id:
            return Response({'detail': 'This ticket is already assigned to that staff member.'}, status=400)

        with transaction.atomic():
            outgoing_staff_id = ticket.assigned_staff_id
            outgoing_staff = ticket.assigned_staff
            if outgoing_staff_id:
                TicketAssignment.objects.filter(
                    ticket=ticket, staff_id=outgoing_staff_id
                ).update(
                    status='transferred', responded_at=timezone.now(), transferred_to=new_staff,
                )

            incoming, created = TicketAssignment.objects.get_or_create(
                ticket=ticket, staff=new_staff,
                defaults={'status': 'pending'},
            )
            if not created:
                incoming.status = 'pending'
                incoming.responded_at = None
                incoming.transferred_to = None
                incoming.save(update_fields=['status', 'responded_at', 'transferred_to'])

            # Permanent log entry for this hop.
            log_assignment_event(ticket, 'transferred', staff=outgoing_staff, to_staff=new_staff)

            # Unassigned until the new staff member accepts.
            # escalated/escalated_at/escalation_note are kept as permanent history, not cleared.
            ticket.assigned_staff = None
            ticket.save(update_fields=['assigned_staff', 'updated_at'])

        return Response(TicketSerializer(ticket).data)


class EscalateTicketView(APIView):
    """Hands the ticket to an admin (lowest-id active admin) and sets the escalated flag/note."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        ticket = Ticket.objects.filter(id=pk).first()
        if not ticket:
            return Response({'detail': 'Ticket not found.'}, status=404)
        if not _can_manage_ticket(request.user, ticket):
            return Response({'detail': 'Only the assigned staff member or an admin can escalate this ticket.'}, status=403)

        serializer = EscalateTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get('reason', '')

        admin_user = User.objects.filter(role=User.Role.ADMIN, is_active=True).order_by('id').first()
        if not admin_user:
            return Response({'detail': 'No admin account is available to escalate to.'}, status=400)

        with transaction.atomic():
            outgoing_staff_id = ticket.assigned_staff_id
            outgoing_staff = ticket.assigned_staff
            if outgoing_staff_id and outgoing_staff_id != admin_user.id:
                TicketAssignment.objects.filter(
                    ticket=ticket, staff_id=outgoing_staff_id
                ).update(
                    status='transferred', responded_at=timezone.now(), transferred_to=admin_user,
                )

            incoming, created = TicketAssignment.objects.get_or_create(
                ticket=ticket, staff=admin_user,
                defaults={'status': 'accepted', 'responded_at': timezone.now()},
            )
            if not created:
                incoming.status = 'accepted'
                incoming.responded_at = timezone.now()
                incoming.transferred_to = None
                incoming.save(update_fields=['status', 'responded_at', 'transferred_to'])

            log_assignment_event(ticket, 'escalated', staff=outgoing_staff, to_staff=admin_user, note=reason)

            ticket.assigned_staff = admin_user
            ticket.escalated = True
            ticket.escalated_at = timezone.now()
            ticket.escalation_note = reason
            if ticket.status == 'Open':
                ticket.status = 'In Progress'
            ticket.save(update_fields=[
                'assigned_staff', 'escalated', 'escalated_at', 'escalation_note',
                'status', 'updated_at',
            ])

        return Response(TicketSerializer(ticket).data)


# ---------------------------------------------------------------------------
# Ticket Assignment — offer / accept / decline
# ---------------------------------------------------------------------------

class MyTicketAssignmentsView(generics.ListAPIView):
    """GET /ticket-assignments/mine/ — offers made to the logged-in staff member."""
    serializer_class = TicketAssignmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            TicketAssignment.objects
            .filter(staff=self.request.user)
            .select_related(
                'ticket', 'ticket__category', 'ticket__raised_by',
                'ticket__raised_by__company', 'staff', 'transferred_to',
            )
        )


class TicketAssignmentListView(generics.ListAPIView):
    """Admin/staff overview of every offer made, filterable by status or escalated."""
    serializer_class = TicketAssignmentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def get_queryset(self):
        qs = TicketAssignment.objects.select_related(
            'ticket', 'ticket__category', 'ticket__raised_by',
            'ticket__raised_by__company', 'staff', 'transferred_to',
        )
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        if self.request.query_params.get('escalated') == 'true':
            qs = qs.filter(ticket__escalated=True)

        return qs


class TicketAssignmentPendingCountView(APIView):
    """GET /ticket-assignments/pending-count/ — badge count for the sidebar.
    Admins see the system-wide pending count; staff see their own."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        if getattr(user, 'role', None) == 'admin':
            count = TicketAssignment.objects.filter(status='pending').count()
        else:
            count = TicketAssignment.objects.filter(staff=user, status='pending').count()
        return Response({'count': count})


class AcceptTicketAssignmentView(APIView):
    """Race-safe accept: locks the ticket row, flips other pending offers to 'unavailable'."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, assignment_id):
        assignment = TicketAssignment.objects.filter(id=assignment_id, staff=request.user).first()
        if not assignment:
            return Response({'detail': 'Assignment not found.'}, status=404)
        if assignment.status != 'pending':
            return Response({'detail': 'This offer is no longer pending.'}, status=409)

        with transaction.atomic():
            ticket = Ticket.objects.select_for_update().get(id=assignment.ticket_id)

            if ticket.assigned_staff_id is not None:
                assignment.status = 'unavailable'
                assignment.responded_at = timezone.now()
                assignment.save(update_fields=['status', 'responded_at'])
                log_assignment_event(ticket, 'unavailable', staff=assignment.staff)
                return Response(
                    {'detail': 'This ticket was already accepted by another staff member.'},
                    status=409,
                )

            assignment.status = 'accepted'
            assignment.responded_at = timezone.now()
            assignment.save(update_fields=['status', 'responded_at'])
            log_assignment_event(ticket, 'accepted', staff=assignment.staff)

            ticket.assigned_staff = request.user
            if ticket.status == 'Open':
                ticket.status = 'In Progress'
            ticket.save(update_fields=['assigned_staff', 'status', 'updated_at'])

            # Captured before the bulk update to log each affected staff member's event.
            other_pending = list(
                TicketAssignment.objects.filter(
                    ticket=ticket, status='pending'
                ).exclude(id=assignment.id).select_related('staff')
            )
            TicketAssignment.objects.filter(
                ticket=ticket, status='pending'
            ).exclude(id=assignment.id).update(
                status='unavailable', responded_at=timezone.now()
            )
            for other in other_pending:
                log_assignment_event(ticket, 'unavailable', staff=other.staff)

        return Response(TicketAssignmentSerializer(assignment).data)


class DeclineTicketAssignmentView(APIView):
    """POST /ticket-assignments/<id>/decline/ — staff explicitly opts out;
    doesn't affect other staff's pending offers for the same ticket."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, assignment_id):
        assignment = TicketAssignment.objects.filter(id=assignment_id, staff=request.user).first()
        if not assignment:
            return Response({'detail': 'Assignment not found.'}, status=404)
        if assignment.status != 'pending':
            return Response({'detail': 'This offer is no longer pending.'}, status=409)

        assignment.status = 'declined'
        assignment.responded_at = timezone.now()
        assignment.save(update_fields=['status', 'responded_at'])
        log_assignment_event(assignment.ticket, 'declined', staff=assignment.staff)
        return Response(TicketAssignmentSerializer(assignment).data)


# ---------------------------------------------------------------------------
# Product Master
# ---------------------------------------------------------------------------

class ProductMasterListCreateView(generics.ListCreateAPIView):
    """List/create Product Master entries. Admin only."""
    serializer_class = ProductMasterSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        qs = ProductMaster.objects.all()
        if self.request.query_params.get('include_inactive') != 'true':
            qs = qs.filter(is_active=True)
        return qs


class ProductMasterDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/PUT/DELETE /products/<uuid>/ — admin only."""
    serializer_class = ProductMasterSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    queryset = ProductMaster.objects.all()


class ProductStaffMapView(APIView):
    """Gets/replaces the staff handling each product name (keyed by name, not row id)."""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        mapping = {}
        for row in StaffProduct.objects.values('product_name', 'staff_id'):
            mapping.setdefault(row['product_name'], []).append(row['staff_id'])
        return Response(mapping)

    def post(self, request):
        product_name = (request.data.get('product_name') or '').strip()
        if not product_name:
            return Response({'detail': 'product_name is required.'}, status=400)
        staff_ids = set(request.data.get('staff_ids') or [])

        existing = set(
            StaffProduct.objects.filter(product_name=product_name).values_list('staff_id', flat=True)
        )
        StaffProduct.objects.filter(
            product_name=product_name, staff_id__in=existing - staff_ids
        ).delete()
        StaffProduct.objects.bulk_create([
            StaffProduct(staff_id=sid, product_name=product_name) for sid in staff_ids - existing
        ])
        return Response({
            'product_name': product_name,
            'staff_ids': list(staff_ids),
        })


class PublicProductListView(generics.ListAPIView):
    """Public, unauthenticated, read-only product picker for customer registration."""
    serializer_class = PublicProductSerializer
    permission_classes = [AllowAny]
    queryset = ProductMaster.objects.filter(is_active=True)