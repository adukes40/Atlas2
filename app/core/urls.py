from django.contrib.auth import views as auth_views
from django.urls import include, path

from devices import views as device_views
from integrations import views as integration_views
from sso import views as sso_views

from . import views

urlpatterns = [
    path("", device_views.device360, name="device360"),
    path("lookups/close/", device_views.close_lookup, name="close-lookup"),
    path("", include("reports.urls")),
    path("settings/", views.settings_page, name="settings"),
    path("settings/users/", views.users, name="users"),
    path("settings/users/add/", views.user_edit, name="user-add"),
    path("settings/users/<int:pk>/", views.user_edit, name="user-edit"),
    path("settings/users/<int:pk>/delete/", views.user_delete, name="user-delete"),
    path("settings/incident-iq/", integration_views.incidentiq_settings, name="settings-incidentiq"),
    path("settings/google/", integration_views.google_settings, name="settings-google"),
    path("settings/mist/", integration_views.mist_settings, name="settings-mist"),
    path("settings/meraki/", integration_views.meraki_settings, name="settings-meraki"),
    path("settings/classlink/", sso_views.classlink_settings, name="settings-classlink"),
    path("sso/classlink/", sso_views.start, name="saml-start"),
    path("sso/classlink/acs/", sso_views.acs, name="saml-acs"),
    path("sso/classlink/metadata/", sso_views.metadata, name="saml-metadata"),
    path("system/", views.system, name="system"),
    path("appearance/", views.appearance, name="appearance"),
    path("theme.css", views.uploaded_theme, name="uploaded-theme"),
    path("healthz/", views.healthz, name="healthz"),
    path("login/", sso_views.SignIn.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
