from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.device360, name="device360"),
    path("reports/", views.reports, name="reports"),
    path("settings/", views.settings_page, name="settings"),
    path("system/", views.system, name="system"),
    path("appearance/", views.appearance, name="appearance"),
    path("healthz/", views.healthz, name="healthz"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="core/login.html", redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
