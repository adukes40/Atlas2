from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import tabs
from core.models import Organization

from .models import LookupLog
from .person import find_people, person360 as build_person
from .service import NOT_FOUND, lookup


@login_required
def device360(request):
    query = request.GET.get("q", "").strip()[:100]
    context = {"section": "device360", "query": query}

    if query:
        result = lookup(query)
        device = result.device
        LookupLog.objects.create(
            user=request.user,
            username=request.user.get_username(),
            query=query,
            found=bool(result.devices),
            key=device.key if device else "",
            label=device.label[:200] if device else "",
        )
        if result.state == NOT_FOUND:
            # Not a tag or serial: maybe a person's name, email or ID.
            people = find_people(query)
            if len(people) == 1:
                return redirect("person360", user_id=people[0].user_id)
            context["people"] = people
        if device:
            tabs.open_tab(request.session, device.key, device.label)
        context.update(result=result, device=device, current_key=device.key if device else "")
        if result.chromebook:
            context["wan_on_network"] = Organization.load().on_network(result.chromebook.wan_ip)

    context["open_lookups"] = tabs.opened(request.session)
    return render(request, "devices/device360.html", context)


@require_POST
@login_required
def close_lookup(request):
    key = request.POST.get("key", "")
    tabs.close_tab(request.session, key)
    remaining = tabs.opened(request.session)
    # Closing the one being viewed moves to the next open one, if any.
    if request.POST.get("current") == key or not request.POST.get("current"):
        if remaining:
            return redirect(f"{reverse('device360')}?{urlencode({'q': remaining[-1]['key']})}")
        return redirect("device360")
    return redirect(f"{reverse('device360')}?{urlencode({'q': request.POST['current']})}")


@login_required
def person360(request, user_id):
    result = build_person(user_id[:64])
    context = {"section": "device360", "query": "", "result": result, "person": result.person,
               "open_lookups": tabs.opened(request.session)}
    return render(request, "devices/person360.html", context)
