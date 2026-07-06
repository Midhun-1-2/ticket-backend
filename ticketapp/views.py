from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import StaffAssignment
from authentication.permissions import IsAdminOrStaff

from .models import Category, Ticket, TicketAssignment
from .serializers import CategorySerializer, TicketSerializer, TicketAssignmentSerializer

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

def offer_ticket_to_eligible_staff(ticket):
    """
    Called right after a ticket is created. Finds every staff member
    currently tied to the raising customer's company — either as primary
    staff (StaffAssignment.product_name == '') or specifically for this
    ticket's product — and creates a 'pending' TicketAssignment for each.
    If the customer's company has no current staff assignment, the ticket
    is simply left with no offers (falls to admin to handle manually).
    """
    raiser = ticket.raised_by
    company = getattr(raiser, 'company', None) if raiser else None
    if not company:
        return []

    staff_ids = set(
        StaffAssignment.objects.filter(company=company, is_current=True)
        .filter(Q(product_name='') | Q(product_name=ticket.product))
        .values_list('staff_id', flat=True)
    )

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
    Status changes (e.g. moving to 'Resolved') go through PATCH here.
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
                'ticket__raised_by__company', 'staff',
            )
        )


class TicketAssignmentListView(generics.ListAPIView):
    """GET /ticket-assignments/?status=pending — admin/staff overview of
    every offer made, across all tickets and staff."""
    serializer_class = TicketAssignmentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def get_queryset(self):
        qs = TicketAssignment.objects.select_related(
            'ticket', 'ticket__category', 'ticket__raised_by',
            'ticket__raised_by__company', 'staff',
        )
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
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