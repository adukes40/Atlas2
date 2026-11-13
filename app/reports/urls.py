from django.urls import path

from . import views

urlpatterns = [
    path("reports/", views.overview, name="reports"),
    path("reports/sync/", views.sync_now, name="sync-now"),
    path("reports/saved/<int:pk>/delete/", views.delete_saved, name="delete-saved"),
    path("reports/<slug:slug>/", views.report, name="report"),
    path("reports/<slug:slug>/save/", views.save, name="save-report"),
]
