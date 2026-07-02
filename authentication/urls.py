from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/refresh/", TokenRefreshView.as_view(), name="login-refresh"),
    path("detect-role/", views.DetectRoleView.as_view(), name="detect-role"),
    path("mpin/create/", views.CreateMpinView.as_view(), name="mpin-create"),
]