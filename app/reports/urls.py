from django.urls import path

from . import views

urlpatterns = [
    path("reports/", views.overview, name="reports"),
    path("reports/sync/", views.sync_now, name="sync-now"),
    path("reports/sync/progress/", views.sync_progress, name="sync-progress"),
    path("reports/sync/<int:pk>/cancel/", views.cancel_sync, name="sync-cancel"),
    path("settings/sync-schedules/add/", views.schedule_edit, name="schedule-add"),
    path("settings/sync-schedules/<int:pk>/", views.schedule_edit, name="schedule-edit"),
    path("settings/sync-schedules/<int:pk>/toggle/", views.schedule_toggle, name="schedule-toggle"),
    path("reports/saved/<int:pk>/delete/", views.delete_saved, name="delete-saved"),
    path("reports/switch/<str:serial>/", views.switch, name="switch"),
    path("reports/<slug:slug>/", views.report, name="report"),
    path("reports/<slug:slug>/save/", views.save, name="save-report"),
]
