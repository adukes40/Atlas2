import csv
from datetime import timedelta
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.http import require_POST

from .catalog import BY_SLUG, OPS, REPORTS, grouped, library, parse_filter, query
from .models import DailyCount, NetDevice, SavedReport, SwitchPort, SyncRun
from .sync import request_sync, sync_zone

PAGE_SIZE = 100


def _counts():
    """Each report's count from the latest sync (lookups are counted live)."""
    latest = {}
    for row in DailyCount.objects.order_by("day"):
        latest[row.report] = row.count
    for report in REPORTS:
        if report.base == "lookups":
            latest[report.slug] = query(report, report.default_filters()).count()
    return latest


def _n(number):
    return f"{number:,}" if number is not None else None


def _shared(request, current=""):
    counts = _counts()
    last = SyncRun.last_good()
    newest = SyncRun.objects.exclude(state=SyncRun.REQUESTED).first()
    return {
        "section": "reports",
        "library": [(group, [(r, _n(counts.get(r.slug))) for r in reports]) for group, reports in library()],
        "saved": SavedReport.objects.filter(user=request.user),
        "current": current,
        "data_as_of": last.finished_at.astimezone(sync_zone()).strftime("%b %-d, %-I:%M %p") if last else None,
        "sync_problems": newest.problems if newest and newest.problems else {},
        "sync_waiting": SyncRun.objects.filter(state__in=[SyncRun.REQUESTED, SyncRun.RUNNING]).exists(),
    }


@login_required
def overview(request):
    since = timezone.localdate(timezone=sync_zone()) - timedelta(days=30)
    trends = {}
    for row in DailyCount.objects.filter(day__gte=since):
        trends.setdefault(row.report, []).append(row.count)
    context = _shared(request, "overview")
    tiles = []
    for group, entries in context["library"]:
        cards = []
        for report, count in entries:
            points = trends.get(report.slug, [])
            top = max(points, default=0) or 1
            change = points[-1] - points[-8] if len(points) >= 8 else None
            change = None if change is None else ("no change" if not change else f"{'up' if change > 0 else 'down'} {abs(change):,}")
            cards.append({"report": report, "count": count, "bars": [round(10 + 90 * p / top) for p in points],
                          "change": change})
        tiles.append((group, cards))
    context["tiles"] = tiles
    return render(request, "reports/overview.html", context)


def _url(request, drop=(), **changes):
    """This page's address with some parameters changed. A list value replaces
    every value of that name; None removes it."""
    params = request.GET.copy()
    for name in ("pick", "add_op", "add_value", "replace", "page", *drop):
        params.pop(name, None)
    for name, value in changes.items():
        if value is None:
            params.pop(name, None)
        elif isinstance(value, list):
            params.setlist(name, value)
        else:
            params[name] = value
    return "?" + params.urlencode() if params else "?"


def _value(row, path):
    for part in path.split("__"):
        row = getattr(row, part, None) if not isinstance(row, dict) else row.get(part)
        if row is None:
            return None
    return row


def _show(value, kind):
    if value is None or value == "":
        return "—", ""
    if kind == "when":
        return f"{timesince(value, depth=1)} ago", value.astimezone(sync_zone()).strftime("%b %-d, %Y %-I:%M %p")
    if kind == "day":
        return value.strftime("%b %-d, %Y"), ""
    if kind == "money":
        return f"${value:,.2f}", ""
    if kind == "bool":
        return ("Yes" if value else "No"), ""
    return str(value), ""


def _raw(value, kind):
    if value is None:
        return ""
    if kind == "when":
        return value.astimezone(sync_zone()).strftime("%Y-%m-%d %H:%M")
    if kind == "bool":
        return "Yes" if value else "No"
    return str(value)


LINKED = {"asset_tag", "serial", "query"}


def _link(report, row, column):
    """Device 360 for a Chromebook's tag, serial or search; the switch page
    for a switch's name."""
    if report.base == "ports" and column == "switch_name":
        serial = row.switch_serial
    elif report.base == "gear" and column == "uplink_switch":
        serial = row.uplink_serial
    elif report.base == "gear" and column == "name" and row.kind == "Switch":
        serial = row.serial
    elif report.base in ("devices", "lookups") and column in LINKED and _value(row, column):
        return f"{reverse('device360')}?q={quote(getattr(row, 'key', '') or _value(row, column), safe='')}"
    else:
        return ""
    return reverse("switch", args=[serial]) if serial else ""


@login_required
def report(request, slug):
    report = BY_SLUG.get(slug)
    if not report:
        raise Http404
    fields = report.fields

    # "+ Filter": a field is picked, then how to filter it.
    if request.GET.get("add_field"):
        token = f"{request.GET['add_field']}.{request.GET.get('add_op', '')}.{request.GET.get('add_value', '')}"
        current = request.GET.getlist("f") if "edited" in request.GET else list(report.filters)
        if parse_filter(fields, token):
            # Editing a chip puts the changed filter where the old one was.
            replace = request.GET.get("replace")
            if replace in current:
                current[current.index(replace)] = token
            else:
                current.append(token)
        else:
            messages.error(request, "That filter needs a value Atlas can use. Days and amounts are numbers.")
        return redirect(request.path + _url(request, drop=("add_field",), f=current, edited="1"))

    tokens = request.GET.getlist("f") if "edited" in request.GET else list(report.filters)
    filters = [f for f in (parse_filter(fields, t) for t in tokens) if f]
    columns = [c for c in request.GET.getlist("c") if c in fields] or list(report.columns)
    sort = request.GET.get("o", report.sort)
    group_key = request.GET.get("g", report.group_by)
    group_key = group_key if group_key in fields else ""

    if request.GET.get("format") == "csv":
        return _csv(report, filters, columns, sort, group_key)

    filtered_paths = {f.field.key for f in filters}
    chips = [
        {"filter": f, "remove": _url(request, f=[t.token for t in filters if t is not f], edited="1"),
         "ops": [(op, words.replace("{v}", "…")) for op, words in OPS[f.field.kind].items()]}
        for f in filters
    ]
    context = _shared(request, slug)
    context.update(
        report=report,
        chips=chips,
        fields=sorted(fields.values(), key=lambda f: (f.system, f.label)),
        columns=[fields[c] for c in columns],
        column_keys=columns,
        group_key=group_key,
        csv_url=_url(request, format="csv"),
        tokens=[f.token for f in filters],
        edited="edited" in request.GET,
        query_string=request.GET.urlencode(),
        reset_url="?",
    )
    pick = fields.get(request.GET.get("pick", ""))
    if pick:
        context["pick"] = pick
        context["pick_ops"] = [(op, words.replace("{v}", "…")) for op, words in OPS[pick.kind].items()]

    if group_key:
        rows, path = grouped(report, filters, group_key)
        groups = list(rows[:500])
        context["groups"] = [
            {"label": _show(g[path], fields[group_key].kind)[0], "count": g["n"],
             "url": _url(request, drop=("g",), g="", edited="1",
                         f=[f.token for f in filters] + [f"{group_key}.{'is' if fields[group_key].kind == 'text' else 'eq'}.{g[path]}"])
             if fields[group_key].kind in ("text", "number") and g[path] not in (None, "") else ""}
            for g in groups
        ]
        context["total"] = sum(g["n"] for g in groups)
        for g in context["groups"]:
            g["count"] = _n(g["count"])
        context["group_label"] = fields[group_key].label
        context["ungroup_url"] = _url(request, g="")
    else:
        page = Paginator(query(report, filters, sort), PAGE_SIZE).get_page(request.GET.get("page"))
        context["page"] = page
        context["total"] = page.paginator.count
        context["headers"] = [
            {"field": fields[c], "sorted": sort.lstrip("-") == c, "down": sort.startswith("-"),
             "url": _url(request, o=("-" + c) if sort == c else c), "hit": c in filtered_paths}
            for c in columns
        ]
        context["rows"] = [
            [
                {"text": shown[0], "title": shown[1], "hit": c in filtered_paths,
                 "link": _link(report, row, c)}
                for c in columns
                for shown in [_show(_value(row, fields[c].path), fields[c].kind)]
            ]
            for row in page.object_list
        ]
        if page.has_previous():
            context["prev_url"] = _url(request, page=page.previous_page_number())
        if page.has_next():
            context["next_url"] = _url(request, page=page.next_page_number())
    context["total_display"] = _n(context["total"])
    return render(request, "reports/report.html", context)


@login_required
def switch(request, serial):
    """One switch drawn like its faceplate, with the chosen port's settings."""
    ports = list(SwitchPort.objects.filter(switch_serial=serial).order_by("pk"))
    if not ports:
        raise Http404
    device = NetDevice.objects.filter(serial=serial, kind="Switch").first()
    aps = {d.mac: d for d in NetDevice.objects.filter(mac__in=[p.neighbor_mac for p in ports if p.neighbor_mac])}
    chosen = next((p for p in ports if p.port_id == request.GET.get("port")), None) \
        or next((p for p in ports if p.neighbor_kind == "Access point"), ports[0])
    for port in ports:
        port.plugged = aps.get(port.neighbor_mac)
        port.classes = " ".join(c for c, on in (
            ("up", port.link == "Connected"), ("ap", port.neighbor_kind == "Access point"),
            ("trunk", port.mode == "Trunk"), ("poe", port.poe_in_use), ("off", port.link == "Disabled"),
            ("on", port is chosen)) if on)
    context = _shared(request, "switches")
    context.update(
        key=[("up", "Up"), ("up ap", "Access point"), ("trunk", "Trunk"), ("poe", "Drawing PoE"), ("", "Down")],
        switch=device, first=ports[0], ports=ports, chosen=chosen,
        faceplate=[p for p in ports if p.number], extra=[p for p in ports if not p.number],
        up=sum(p.link == "Connected" for p in ports), plugged=[p for p in ports if p.neighbor and p is not chosen],
    )
    return render(request, "reports/switch.html", context)


class _Echo:
    def write(self, value):
        return value


def _csv(report, filters, columns, sort, group_key):
    fields = report.fields
    writer = csv.writer(_Echo())
    if group_key:
        rows, path = grouped(report, filters, group_key)
        lines = [[fields[group_key].label, "Count"]] + [[_raw(r[path], fields[group_key].kind), r["n"]] for r in rows]
    else:
        lines = _chain([[fields[c].label for c in columns]], (
            [_raw(_value(row, fields[c].path), fields[c].kind) for c in columns]
            for row in query(report, filters, sort).iterator(chunk_size=2000)
        ))
    stamp = timezone.localtime(timezone=sync_zone()).strftime("%Y-%m-%d")
    response = StreamingHttpResponse((writer.writerow(line) for line in lines), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="atlas-{report.slug}-{stamp}.csv"'
    return response


def _chain(first, rest):
    yield from first
    yield from rest


@login_required
@require_POST
def save(request, slug):
    if slug not in BY_SLUG:
        raise Http404
    name = request.POST.get("name", "").strip()[:100]
    if not name:
        messages.error(request, "Give the report a name to save it.")
        return redirect(reverse("report", args=[slug]) + "?" + request.POST.get("query", ""))
    saved = SavedReport.objects.create(user=request.user, name=name, report=slug, query=request.POST.get("query", "")[:2000])
    messages.success(request, f"Saved as “{saved.name}”.")
    return redirect(reverse("report", args=[slug]) + "?" + saved.query)


@login_required
@require_POST
def delete_saved(request, pk):
    get_object_or_404(SavedReport, pk=pk, user=request.user).delete()
    messages.success(request, "Saved report removed.")
    return redirect("reports")


@login_required
@require_POST
def sync_now(request):
    request_sync(request.user.get_username())
    messages.success(request, "Sync queued. It usually takes a few minutes; reports update when it finishes.")
    target = request.POST.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else "reports")
