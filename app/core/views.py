import django
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .context import MODE_COOKIE, MODES
from .forms import OrganizationForm, UserForm
from .models import Organization


def _database_ok():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:
        return False


def healthz(request):
    ok = _database_ok()
    return JsonResponse({"ok": ok}, status=200 if ok else 503)


@login_required
def reports(request):
    return render(request, "core/reports.html", {"section": "reports"})


@login_required
def settings_page(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    org = Organization.load()
    form = OrganizationForm(request.POST or None, instance=org)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("settings")
    users = get_user_model().objects.filter(is_active=True).count()
    return render(request, "core/settings.html", {"section": "settings", "form": form, "active_users": users})


@login_required
def users(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    people = get_user_model().objects.order_by("-is_active", "username")
    return render(request, "core/users.html", {"section": "settings", "people": people})


@login_required
def user_edit(request, pk=None):
    if not request.user.is_superuser:
        raise PermissionDenied
    person = get_object_or_404(get_user_model(), pk=pk) if pk else None
    form = UserForm(request.POST or None, instance=person, editor=request.user)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        if saved.pk == request.user.pk:
            update_session_auth_hash(request, saved)  # changing your own password keeps you signed in
        messages.success(request, f"Saved {saved.get_username()}.")
        return redirect("users")
    return render(request, "core/user_form.html", {"section": "settings", "form": form, "person": person})


@login_required
def system(request):
    return render(
        request,
        "core/system.html",
        {
            "section": "system",
            "database_ok": _database_ok(),
            "database_vendor": connection.vendor,
            "django_version": django.get_version(),
            "now": timezone.now(),
        },
    )


@require_POST
def appearance(request):
    """Remember Light, Dark or Auto (follow the device) in a cookie."""
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = "/"
    response = redirect(target)
    mode = request.POST.get("mode")
    if mode in MODES:
        response.set_cookie(MODE_COOKIE, mode, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return response
