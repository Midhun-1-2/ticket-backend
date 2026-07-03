from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    # Auth
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/refresh/", TokenRefreshView.as_view(), name="login-refresh"),
    path("detect-role/", views.DetectRoleView.as_view(), name="detect-role"),
    path("mpin/create/", views.CreateMpinView.as_view(), name="mpin-create"),

    # Onboarding — public
    path("onboarding/draft/", views.OnboardingDraftView.as_view(), name="onboarding-draft"),
    path("onboarding/submit/", views.OnboardingSubmitView.as_view(), name="onboarding-submit"),

    # Onboarding — admin/staff review
    path("onboarding/pending/", views.PendingCompanyListView.as_view(), name="onboarding-pending"),
    path("onboarding/<int:company_id>/", views.CompanyDetailView.as_view(), name="onboarding-detail"),
    path("onboarding/<int:company_id>/approve/", views.ApproveCompanyView.as_view(), name="onboarding-approve"),
    path("onboarding/<int:company_id>/reject/", views.RejectCompanyView.as_view(), name="onboarding-reject"),

    # Staff Management
    path("staff/", views.StaffListCreateView.as_view(), name="staff-list-create"),
    path("staff/<int:staff_id>/", views.StaffDetailView.as_view(), name="staff-detail"),
    path("staff/<int:staff_id>/toggle-status/", views.StaffToggleStatusView.as_view(), name="staff-toggle-status"),
    path("staff-roles/", views.StaffRoleListCreateView.as_view(), name="staff-roles-list-create"),
    path("staff-roles/<int:role_id>/", views.StaffRoleDeleteView.as_view(), name="staff-roles-delete"),
]