import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .crypto import EncryptionNotConfigured
from .forms import IncidentIQForm
from .incidentiq.client import IncidentIQError
from .models import IncidentIQConfig

logger = logging.getLogger(__name__)


@login_required
def incidentiq_settings(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    config = IncidentIQConfig.load()
    form = IncidentIQForm(
        request.POST or None,
        initial={"base_url": config.base_url, "site_id": config.site_id, "product_id": config.product_id},
        has_token=config.has_token,
    )

    if request.method == "POST" and form.is_valid():
        config.base_url = form.cleaned_data["base_url"]
        config.site_id = form.cleaned_data["site_id"]
        config.product_id = form.cleaned_data["product_id"]
        try:
            if form.cleaned_data["token"]:
                config.set_token(form.cleaned_data["token"])
            config.save()
            total = config.client().check()
        except EncryptionNotConfigured as error:
            form.add_error(None, str(error))
        except IncidentIQError as error:
            config.record_error(str(error))
            messages.error(request, f"Saved, but the connection test failed. {error}")
            return redirect("settings-incidentiq")
        except Exception as error:  # noqa: BLE001 - never show a bare error page for a connection test
            logger.exception("Incident IQ connection test failed unexpectedly")
            problem = f"Atlas hit an unexpected problem ({type(error).__name__}). The details are in the server log."
            config.record_error(problem)
            messages.error(request, f"Saved, but the connection test failed. {problem}")
            return redirect("settings-incidentiq")
        else:
            config.record_ok()
            messages.success(request, f"Connected. Incident IQ reports {total:,} assets.")
            return redirect("settings-incidentiq")

    return render(
        request, "integrations/incidentiq.html", {"section": "settings", "form": form, "config": config}
    )
