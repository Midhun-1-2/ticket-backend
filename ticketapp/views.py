from rest_framework import generics, permissions
from rest_framework.response import Response
from .models import Category, Ticket
from .serializers import CategorySerializer, TicketSerializer


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
        qs = Ticket.objects.select_related('category', 'raised_by').prefetch_related('attachments')
        if getattr(user, 'role', None) == 'customer':
            qs = qs.filter(raised_by=user)
        return qs

    def perform_create(self, serializer):
        ticket = serializer.save(raised_by=self.request.user)
        for f in self.request.FILES.getlist('attachments'):
            ticket.attachments.create(file=f)


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
        qs = Ticket.objects.select_related('category', 'raised_by').prefetch_related('attachments')
        if getattr(user, 'role', None) == 'customer':
            qs = qs.filter(raised_by=user)
        return qs