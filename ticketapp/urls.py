from django.urls import path
from .views import (
    CategoryListCreateView,
    CategoryDetailView,
    TicketListCreateView,
    TicketDetailView,
    ProductMasterListCreateView,
    ProductMasterDetailView,
    PublicProductListView,
)

urlpatterns = [
    path('categories/', CategoryListCreateView.as_view(), name='category-list-create'),
    path('categories/<uuid:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    path('tickets/', TicketListCreateView.as_view(), name='ticket-list-create'),
    path('tickets/<uuid:pk>/', TicketDetailView.as_view(), name='ticket-detail'),

    path('products/', ProductMasterListCreateView.as_view(), name='product-list-create'),
    path('products/<uuid:pk>/', ProductMasterDetailView.as_view(), name='product-detail'),

    path('public-products/', PublicProductListView.as_view(), name='public-product-list'),

]