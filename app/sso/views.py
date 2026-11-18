import logging
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import saml
from .forms import ClassLinkForm
from .models import ClassLinkConfig, UsedAssertion

logger = logging.getLogger(__name__)


def _safe(request, target):
    ok = url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return target if target and ok else settings.LOGIN_REDIRECT_URL


def _refuse(request, message):
    return render(request, "core/login.html", {"sso_error": message, "classlink_on": True}, status=403)


class SignIn(LoginView):
    """The usual sign-in page, plus a ClassLink button when it is on. Passwords always stay available."""

    template_name = "core/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs, classlink_on=ClassLinkConfig.load().ready)


def metadata(request):
    """Atlas's own SAML metadata, to paste into the ClassLink app."""
    return HttpResponse(saml.sp_metadata(request), content_type="text/xml")


def start(request):
    """Sign in with ClassLink, from Atlas's sign-in page."""
    config = ClassLinkConfig.load()
    if not config.ready:
        return redirect("login")
    return redirect(saml.login_url(request, config, _safe(request, request.GET.get("next", ""))))


@csrf_exempt  # ClassLink posts here from its own site; the signed response is the proof
@require_POST
def acs(request):
    config = ClassLinkConfig.load()
    if not config.ready:
        return _refuse(request, "ClassLink sign-in is turned off in Atlas.")
    try:
        assertion_id, not_after, identity = saml.read_response(request, config)
        expires = datetime.fromtimestamp(not_after, UTC) if not_after else timezone.now() + timedelta(hours=1)
        if not UsedAssertion.first_use(assertion_id, expires):
            raise saml.SignInRefused("That ClassLink sign-in was already used. Start again from ClassLink.")
        user = saml.find_or_create_user(identity, config.create_accounts)
    except saml.SignInRefused as error:
        logger.warning("ClassLink sign-in refused: %s", error)
        return _refuse(request, str(error))
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(_safe(request, request.POST.get("RelayState", "")))


@login_required
def classlink_settings(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    config = ClassLinkConfig.load()
    form = ClassLinkForm(
        request.POST or None,
        initial={"metadata": config.metadata, "enabled": config.enabled, "create_accounts": config.create_accounts},
    )
    if request.method == "POST" and form.is_valid():
        for name in ("metadata", "enabled", "create_accounts"):
            setattr(config, name, form.cleaned_data[name])
        config.save()
        messages.success(request, "ClassLink sign-in saved." + (" It is on." if config.ready else " It is off."))
        return redirect("settings-classlink")
    values = saml.sp_values(request)
    return render(
        request,
        "sso/settings.html",
        {"section": "settings", "form": form, "config": config, "values": values,
         "launch_url": saml.base_url(request) + reverse("saml-start")},
    )
