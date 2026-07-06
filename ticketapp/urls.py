from django.urls import path
from .views import (
    CategoryListCreateView,
    CategoryDetailView,
    TicketListCreateView,
    TicketDetailView,
    MyTicketAssignmentsView,
    TicketAssignmentListView,
    TicketAssignmentPendingCountView,
    AcceptTicketAssignmentView,
    DeclineTicketAssignmentView,
)

urlpatterns = [
    path('categories/', CategoryListCreateView.as_view(), name='category-list-create'),
    path('categories/<uuid:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    path('tickets/', TicketListCreateView.as_view(), name='ticket-list-create'),
    path('tickets/<uuid:pk>/', TicketDetailView.as_view(), name='ticket-detail'),

    path('ticket-assignments/', TicketAssignmentListView.as_view(), name='ticket-assignment-list'),
    path('ticket-assignments/mine/', MyTicketAssignmentsView.as_view(), name='ticket-assignment-mine'),
    path('ticket-assignments/pending-count/', TicketAssignmentPendingCountView.as_view(), name='ticket-assignment-pending-count'),
    path('ticket-assignments/<uuid:assignment_id>/accept/', AcceptTicketAssignmentView.as_view(), name='ticket-assignment-accept'),
    path('ticket-assignments/<uuid:assignment_id>/decline/', DeclineTicketAssignmentView.as_view(), name='ticket-assignment-decline'),
]