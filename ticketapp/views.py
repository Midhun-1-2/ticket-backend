from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import StaffAssignment
from authentication.permissions import IsAdmin, IsAdminOrStaff

from .models import Category, Ticket, TicketAssignment, ProductMaster
from .serializers import (
    CategorySerializer,
    TicketSerializer,
    TicketStatusUpdateSerializer,
    TransferTicketSerializer,
    EscalateTicketSerializer,
    TicketAssignmentSerializer,
    ProductMasterSerializer,
    PublicProductSerializer,
    _product_in_use,
)

User = get_user_model()


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
        # Categories referenced by any ticket can't be removed — the FK is
        # PROTECT anyway (see Ticket.category), so this just gives a clean
        # 409 with a message instead of letting an IntegrityError bubble up.
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
    """
    Staff currently tied to the ticket's raising company — either as
    primary staff (StaffAssignment.product_name == '') or scoped to this
    ticket's product. This is the same pool a ticket is originally offered
    to (see offer_ticket_to_eligible_staff below); reused here so the
    Transfer picker can group "assigned to this customer" vs "other staff"
    without duplicating the query.
    """
    raiser = ticket.raised_by
    company = getattr(raiser, 'company', None) if raiser else None
    if not company:
        return set()

    return set(
        StaffAssignment.objects.filter(company=company, is_current=True)
        .filter(Q(product_name='') | Q(product_name=ticket.product))
        .values_list('staff_id', flat=True)
    )


def offer_ticket_to_eligible_staff(ticket):
    """
    Called right after a ticket is created. Creates a 'pending'
    TicketAssignment for every staff member returned by
    get_eligible_staff_ids(). If the customer's company has no current
    staff assignment, the ticket is simply left with no offers (falls to
    admin to handle manually).
    """
    staff_ids = get_eligible_staff_ids(ticket)

    created = []
    for staff_id in staff_ids:
        obj, _ = TicketAssignment.objects.get_or_create(
            ticket=ticket, staff_id=staff_id, defaults={'status': 'pending'}
        )
        created.append(obj)
    return created


class TicketListCreateView(generics.ListCreateAPIView):
    """
    GET  /tickets/   — customers see only their own tickets; staff/admin see all
    POST /tickets/   — multipart form: subject, category, priority, description,
                        product, and any number of 'attachments' files
    """
    serializer_class = TicketSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Ticket.objects.select_related(
            'category', 'raised_by', 'assigned_staff'
        ).prefetch_related('attachments')
        if getattr(user, 'role', None) == 'customer':
            qs = qs.filter(raised_by=user)
        return qs

    def perform_create(self, serializer):
        ticket = serializer.save(raised_by=self.request.user)
        for f in self.request.FILES.getlist('attachments'):
            ticket.attachments.create(file=f)
        offer_ticket_to_eligible_staff(ticket)


class TicketDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/PUT/DELETE /tickets/<uuid>/
    Customers can only access their own tickets; staff/admin can access any.
    Used by the Ticket Assignment detail popup to pull full ticket info
    (description, attachments) for both the assignee's own tickets and
    ones taken by another staff member.
    """
    serializer_class = TicketSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Ticket.objects.select_related(
            'category', 'raised_by', 'assigned_staff'
        ).prefetch_related('attachments')
        if getattr(user, 'role', None) == 'customer':
            qs = qs.filter(raised_by=user)
        return qs

    def perform_update(self, serializer):
        # status is read_only on TicketSerializer, so it only ever changes
        # here via a raw PATCH like AllTickets.jsx sends — mirror the same
        # closed_at bookkeeping TicketStatusUpdateView does, so closing a
        # ticket from either screen behaves identically.
        new_status = self.request.data.get('status')
        instance = serializer.instance
        if new_status == 'Closed' and instance.status != 'Closed':
            serializer.save(closed_at=timezone.now())
        elif new_status and new_status != 'Closed' and instance.closed_at is not None:
            serializer.save(closed_at=None)
        else:
            serializer.save()

def _can_manage_ticket(user, ticket):
    """
    Who can change a ticket's status, transfer it, or escalate it:
      - Unassigned ticket -> admin only (no one else has claimed it yet).
      - Assigned to an admin (still actively escalated) -> admin only.
      - Assigned to a real staff member -> ONLY that staff member. Once a
        ticket has been handed to staff, it's their ticket to run —
        admin reverts to a read-only view rather than keeping a
        permanent override, so responsibility doesn't stay ambiguous.
    """
    if ticket.assigned_staff_id is None:
        return getattr(user, 'role', None) == 'admin'
    if getattr(ticket.assigned_staff, 'role', None) == 'admin':
        return getattr(user, 'role', None) == 'admin'
    return ticket.assigned_staff_id == user.id


class TicketEligibleStaffView(APIView):
    """
    GET /tickets/<id>/eligible-staff/
    Returns the staff ids currently tied to this ticket's company (the
    pool it was originally offered to) — used purely to group the
    Transfer picker into "Assigned to this customer" vs "Other staff",
    not to restrict who can actually be transferred to.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def get(self, request, pk):
        ticket = Ticket.objects.filter(id=pk).first()
        if not ticket:
            return Response({'detail': 'Ticket not found.'}, status=404)
        return Response({'staff_ids': list(get_eligible_staff_ids(ticket))})


class TicketStatusUpdateView(APIView):
    """
    PATCH /tickets/<id>/status/  { status }
    Lets the assigned staff member (or an admin) move a ticket through
    In Progress -> On Hold -> Resolved -> Closed. 'Open' is intentionally
    not a valid target here — see TicketStatusUpdateSerializer.
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        ticket = Ticket.objects.filter(id=pk).first()
        if not ticket:
            return Response({'detail': 'Ticket not found.'}, status=404)
        if not _can_manage_ticket(request.user, ticket):
            return Response({'detail': 'Only the assigned staff member or an admin can update this ticket.'}, status=403)

        serializer = TicketStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data['status']

        ticket.status = new_status
        update_fields = ['status', 'updated_at']

        if new_status == 'Closed':
            ticket.closed_at = timezone.now()
            update_fields.append('closed_at')
        elif ticket.closed_at is not None:
            # Reopened after being closed — clear the stale timestamp so
            # closed_at always reflects the *current* closure, not a past one.
            ticket.closed_at = None
            update_fields.append('closed_at')

        ticket.save(update_fields=update_fields)
        return Response(TicketSerializer(ticket).data)


class TransferTicketView(APIView):
    """
    POST /tickets/<id>/transfer/  { staff_id }
    Hands a ticket to another staff member as a fresh PENDING offer — the
    new staff must explicitly accept it (or decline it) via the same
    accept/decline endpoints used for regular offers. The ticket is
    unassigned in the interim (assigned_staff cleared), which is exactly
    what makes AcceptTicketAssignmentView's existing race-safety logic
    work unmodified: it only blocks an accept when the ticket already has
    an assignee, and during a transfer it deliberately doesn't.

    The outgoing staff's TicketAssignment row flips to 'transferred' (with
    transferred_to set) immediately, so they lose management rights on
    this ticket right away — only an admin can act on it until the new
    staff member accepts.
    """
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

            # Unassigned until the new staff member accepts — see
            # AcceptTicketAssignmentView, which only rejects an accept when
            # ticket.assigned_staff_id is already set.
            #
            # NOTE: escalated/escalated_at/escalation_note are deliberately
            # NOT cleared here. Escalation is kept as permanent history —
            # the ticket stays listed in the Escalated panel and keeps its
            # banner/note forever, even after being transferred onward.
            # What changes is just who currently holds it; the frontend
            # derives "current status" (e.g. "Transferred to X") from
            # assigned_staff + the assignment chain, not from `escalated`.
            ticket.assigned_staff = None
            ticket.save(update_fields=['assigned_staff', 'updated_at'])

        return Response(TicketSerializer(ticket).data)


class EscalateTicketView(APIView):
    """
    POST /tickets/<id>/escalate/  { reason }
    Escalating actually hands the ticket to an admin — same handoff
    mechanics as TransferTicketView (outgoing staff's row flips to
    'transferred', admin gets an 'accepted' row) — plus it sets the
    `escalated` flag/note so admins can spot how the ticket got to them.
    If there are multiple admin accounts, it picks one (the
    lowest-id active admin) rather than asking the escalating staff
    member to choose.
    """
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
    """GET /ticket-assignments/?status=pending — admin/staff overview of
    every offer made, across all tickets and staff.
    GET /ticket-assignments/?escalated=true — same, but filtered to rows
    whose ticket has been escalated, regardless of the row's own status
    (an escalated ticket's admin row is 'accepted', not 'pending', so this
    needs its own filter rather than reusing the status one)."""
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
    """
    POST /ticket-assignments/<id>/accept/
    Race-safe: locks the ticket row, and only lets the accept through if no
    one else has claimed it yet. Every other pending offer for the same
    ticket is flipped to 'unavailable' in the same transaction.
    """
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
                return Response(
                    {'detail': 'This ticket was already accepted by another staff member.'},
                    status=409,
                )

            assignment.status = 'accepted'
            assignment.responded_at = timezone.now()
            assignment.save(update_fields=['status', 'responded_at'])

            ticket.assigned_staff = request.user
            if ticket.status == 'Open':
                ticket.status = 'In Progress'
            ticket.save(update_fields=['assigned_staff', 'status', 'updated_at'])

            TicketAssignment.objects.filter(
                ticket=ticket, status='pending'
            ).exclude(id=assignment.id).update(
                status='unavailable', responded_at=timezone.now()
            )

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
        return Response(TicketAssignmentSerializer(assignment).data)


# ---------------------------------------------------------------------------
# Product Master
# ---------------------------------------------------------------------------

class ProductMasterListCreateView(generics.ListCreateAPIView):
    """
    GET  /products/   — admin only. ?include_inactive=true to see disabled products too.
    POST /products/   — {name, version, activation_date}. Used both for
                        creating a brand-new product AND for adding a new
                        version of an existing one (same name, different
                        version) — see ProductMasterPage.jsx's
                        "Add New Version" action.
    """
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

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        # Same lock pattern as CategoryDetailView.destroy — refuse the
        # delete with a clean 409 rather than orphaning tickets/company
        # records that still reference this product by name.
        if _product_in_use(instance):
            return Response(
                {"detail": "This product is in use by existing tickets or customer records and cannot be deleted."},
                status=409,
            )
        instance.delete()
        return Response(status=204)


class PublicProductListView(generics.ListAPIView):
    """
    GET /public-products/ — public, unauthenticated, read-only.
    Used by the customer registration form (Onboarding.jsx) to populate
    the product picker before the user has an account. Only exposes
    active products, and only id/name/version — nothing sensitive.
    """
    serializer_class = PublicProductSerializer
    permission_classes = [AllowAny]
    queryset = ProductMaster.objects.filter(is_active=True)