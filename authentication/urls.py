from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    # Auth
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/refresh/", TokenRefreshView.as_view(), name="login-refresh"),
    path("detect-role/", views.DetectRoleView.as_view(), name="detect-role"),
    path("mpin/create/", views.CreateMpinView.as_view(), name="mpin-create"),
    path('logout/', views.LogoutView.as_view(), name='logout'),

    # Onboarding — public
    path("onboarding/draft/", views.OnboardingDraftView.as_view(), name="onboarding-draft"),
    path("onboarding/submit/", views.OnboardingSubmitView.as_view(), name="onboarding-submit"),

    # Onboarding — admin/staff review
    path("onboarding/pending/", views.PendingCompanyListView.as_view(), name="onboarding-pending"),
    path("onboarding/<int:company_id>/", views.CompanyDetailView.as_view(), name="onboarding-detail"),
    path("onboarding/<int:company_id>/approve/", views.ApproveCompanyView.as_view(), name="onboarding-approve"),
    path("onboarding/<int:company_id>/reject/", views.RejectCompanyView.as_view(), name="onboarding-reject"),
    path("onboarding/<int:company_id>/revoke/", views.RevokeCompanyApprovalView.as_view(), name="onboarding-revoke"),
    path("onboarding/<int:company_id>/verify-products/", views.VerifyProductsView.as_view(), name="onboarding-verify-products"),
    path("onboarding/<int:company_id>/assign-staff/", views.AssignStaffView.as_view(), name="onboarding-assign-staff"),
    # Onboarding — public
    path("onboarding/draft/", views.OnboardingDraftView.as_view(), name="onboarding-draft"),
    path("onboarding/submit/", views.OnboardingSubmitView.as_view(), name="onboarding-submit"),
    path("check-mobile/", views.CheckMobileAvailabilityView.as_view(), name="check-mobile"),

    # Staff Management
    path("staff/", views.StaffListCreateView.as_view(), name="staff-list-create"),
    path("staff/<int:staff_id>/", views.StaffDetailView.as_view(), name="staff-detail"),
    path("staff/<int:staff_id>/toggle-status/", views.StaffToggleStatusView.as_view(), name="staff-toggle-status"),
    path("staff/<int:staff_id>/assigned-customers/", views.StaffAssignedCustomersView.as_view(), name="staff-assigned-customers"),
    path("staff/<int:staff_id>/assigned-tickets/", views.StaffAssignedTicketsView.as_view(), name="staff-assigned-tickets"),
    path("staff-roles/", views.StaffRoleListCreateView.as_view(), name="staff-roles-list-create"),
    path("staff-roles/<int:role_id>/", views.StaffRoleDeleteView.as_view(), name="staff-roles-delete"),
    path("staff-departments/", views.StaffDepartmentListCreateView.as_view(), name="staff-departments-list-create"),
    path("staff-departments/<int:department_id>/", views.StaffDepartmentDeleteView.as_view(), name="staff-departments-delete"),

    #Customer Management
    path("customers/", views.CustomerListView.as_view(), name="customer-list"),
    path("customers/<int:pk>/", views.CustomerDetailView.as_view(), name="customer-detail"),
    path("customers/<int:pk>/deactivate/", views.CustomerDeactivateView.as_view(), name="customer-deactivate"),
    path("customers/<int:pk>/products/", views.CustomerAddProductView.as_view(), name="customer-add-product"),
    path("customers/<int:pk>/products/<int:product_id>/", views.CustomerRemoveProductView.as_view(), name="customer-remove-product"),

    path('my-products/', views.MyProductsView.as_view(), name='my-products'),

    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("mpin/change/request-otp/", views.RequestMpinChangeOtpView.as_view(), name="mpin-change-request-otp"),
    path("mpin/change/verify-otp/", views.VerifyMpinChangeOtpView.as_view(), name="mpin-change-verify-otp"),
    path("mpin/change/", views.ChangeMpinView.as_view(), name="mpin-change"),

    # Forgot M-PIN — unauthenticated, used from the login screen
    path("mpin/forgot/request-otp/", views.RequestForgotMpinOtpView.as_view(), name="mpin-forgot-request-otp"),
    path("mpin/forgot/verify-otp/", views.VerifyForgotMpinOtpView.as_view(), name="mpin-forgot-verify-otp"),
    path("mpin/forgot/reset/", views.ResetForgotMpinView.as_view(), name="mpin-forgot-reset"),
]