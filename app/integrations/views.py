from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .crypto import EncryptionNotConfigured
from .forms import IncidentIQForm
from .incidentiq.client import IncidentIQError
from .models import IncidentIQConfig


@login_required
def incidentiq_settings(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    config = IncidentIQConfig.load()
    form = IncidentIQForm(
        request.POST or None, initial={"base_url": config.base_url}, has_token=config.has_token
    )

    if request.method == "POST" and form.is_valid():
        config.base_url = form.cleaned_data["base_url"]
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
        else:
            config.record_ok()
            messages.success(request, f"Connected. Incident IQ reports {total:,} assets.")
            return redirect("settings-incidentiq")

    return render(
        request, "integrations/incidentiq.html", {"section": "settings", "form": form, "config": config}
    )
