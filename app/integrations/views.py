import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .crypto import EncryptionNotConfigured
from .errors import IntegrationError
from .forms import GoogleForm, IncidentIQForm, MerakiForm, MistForm
from .models import GoogleConfig, IncidentIQConfig, MerakiConfig, MistConfig

logger = logging.getLogger(__name__)


def _save_and_test(request, config, form, url_name, template, test):
    """Save the form, then prove the connection works. `test` returns the
    message shown on success. A failed test still saves, and says why."""
    if request.method == "POST" and form.is_valid():
        try:
            form.apply(config)
            config.save()
            message = test(config)
        except EncryptionNotConfigured as error:
            form.add_error(None, str(error))
        except IntegrationError as error:
            config.record_error(str(error))
            messages.error(request, f"Saved, but the connection test failed. {error}")
            return redirect(url_name)
        except Exception as error:  # noqa: BLE001 - never show a bare error page for a connection test
            logger.exception("%s connection test failed unexpectedly", config.name)
            problem = f"Atlas hit an unexpected problem ({type(error).__name__}). The details are in the server log."
            config.record_error(problem)
            messages.error(request, f"Saved, but the connection test failed. {problem}")
            return redirect(url_name)
        else:
            config.record_ok()
            messages.success(request, message)
            return redirect(url_name)

    return render(request, template, {"section": "settings", "form": form, "config": config})


def _superuser(view):
    @login_required
    def wrapped(request):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request)

    return wrapped


@_superuser
def incidentiq_settings(request):
    config = IncidentIQConfig.load()
    form = IncidentIQForm(
        request.POST or None,
        initial={"base_url": config.base_url, "site_id": config.site_id, "product_id": config.product_id},
        has_token=config.has_token,
    )

    def test(config):
        return f"Connected. Incident IQ reports {config.client().check():,} assets."

    return _save_and_test(request, config, form, "settings-incidentiq", "integrations/incidentiq.html", test)


def _with_org(config):
    config.org_id, config.org_name = config.client().check()
    config.save()
    return f"Connected to {config.org_name or config.org_id}."


@_superuser
def google_settings(request):
    config = GoogleConfig.load()
    form = GoogleForm(request.POST or None, initial={"admin_email": config.admin_email}, has_token=config.has_token)

    def test(config):
        config.client().check()
        return f"Connected. Google lets Atlas read Chrome devices as {config.admin_email}."

    return _save_and_test(request, config, form, "settings-google", "integrations/connection.html", test)


@_superuser
def mist_settings(request):
    config = MistConfig.load()
    form = MistForm(
        request.POST or None,
        initial={"api_host": config.api_host, "org_id": config.org_id},
        has_token=config.has_token,
    )
    return _save_and_test(request, config, form, "settings-mist", "integrations/connection.html", _with_org)


@_superuser
def meraki_settings(request):
    config = MerakiConfig.load()
    form = MerakiForm(request.POST or None, initial={"org_id": config.org_id}, has_token=config.has_token)
    return _save_and_test(request, config, form, "settings-meraki", "integrations/connection.html", _with_org)
