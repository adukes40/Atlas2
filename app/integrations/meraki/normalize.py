"""Turns Meraki's answer for one MAC into a Sighting."""

from datetime import datetime, timezone

from ..network import Connection, Sighting


def _text(value):
    return str(value).strip() if value not in (None, "") else ""


def _when(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        when = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def normalize_client(raw):
    """The most recent record across networks, or None if there is none."""
    records = [r for r in raw.get("records") or [] if isinstance(r, dict)]
    stamped = [(_when(r.get("lastSeen")), r) for r in records]
    stamped = [(seen, r) for seen, r in stamped if seen]
    if not stamped:
        return None
    last_seen, record = max(stamped, key=lambda pair: pair[0])
    return Sighting(
        source="Meraki",
        place=_text((record.get("network") or {}).get("name")),
        access_point=_text(record.get("recentDeviceName")) or _text(record.get("recentDeviceSerial")),
        ssid=_text(record.get("ssid")),
        ip=_text(record.get("ip")),
        last_seen=last_seen,
        # Meraki also lists Wi-Fi clients on the switch port their access point uses.
        wireless=bool(_text(record.get("ssid"))),
        vlan=_text(record.get("vlan")),
        switch_mac=_text(record.get("recentDeviceMac")),
        switch_port=_text(record.get("switchport")),
        network_id=_text((record.get("network") or {}).get("id")),
        client_id=_text(raw.get("clientId")),
    )


def normalize_port(device, port, policy=None, group_policies=None):
    """The switch port, from Meraki's device, port and client policy answers."""
    policy = policy or {}
    names = {str(g.get("groupPolicyId")): _text(g.get("name")) for g in group_policies or [] if isinstance(g, dict)}
    group = _text(policy.get("groupPolicyId"))
    trunk = _text(port.get("type")).lower() == "trunk"
    return Connection(
        switch=_text(device.get("name")) or _text(device.get("serial")),
        port=_text(port.get("portId")),
        mode="Trunk" if trunk else "Access",
        vlan=_text(port.get("vlan")),
        allowed_vlans=_text(port.get("allowedVlans")) if trunk else "",
        voice_vlan=_text(port.get("voiceVlan")),
        poe=bool(port.get("poeEnabled")),
        policy=(names.get(group) or f"Group policy {group}") if group else _text(policy.get("devicePolicy")),
    )


# The nightly copy of switches, access points and switch ports. Each function
# returns plain field values for the report tables.

KINDS = {"switch": "Switch", "wireless": "Access point"}


def _mac(value):
    plain = "".join(c for c in _text(value).lower() if c in "0123456789abcdef")
    return plain if len(plain) == 12 else ""


def normalize_gear(raw, status="", networks=None):
    """A switch or access point from Meraki's organization device list."""
    return {
        "mac": _mac(raw.get("mac")), "vendor": "Meraki", "kind": KINDS.get(_text(raw.get("productType")), "Other"),
        "name": _text(raw.get("name")) or _text(raw.get("serial")), "model": _text(raw.get("model")),
        "serial": _text(raw.get("serial")), "site": (networks or {}).get(_text(raw.get("networkId")), ""),
        "ip": _text(raw.get("lanIp")), "status": _text(status).capitalize(),
        "firmware": _text(raw.get("firmware")), "url": _text(raw.get("url")),
    }


def _pairs(rows):
    return {_text(r.get("name")): _text(r.get("value")) for r in rows or [] if isinstance(r, dict)}


def neighbor(lldp, cdp):
    """(name, kind, model, mac, ip) of what a port's LLDP or CDP sees."""
    l, c = _pairs(lldp), _pairs(cdp)
    about = l.get("System description") or c.get("Version") or c.get("Platform") or ""
    name = l.get("System name") or c.get("Device ID") or ""
    text = f"{about} {c.get('Platform', '')} {l.get('System capabilities', '')} {c.get('Capabilities', '')}".lower()
    if not (name or about):
        return "", "", "", "", ""
    if "access point" in text:
        kind = "Access point"
    elif "camera" in text:
        kind = "Camera"
    elif "phone" in text:
        kind = "Phone"
    elif "switch" in text or "router" in text or "cisco" in text:
        kind = "Switch"
    else:
        kind = "Other"
    mac = _mac(l.get("Chassis ID")) or _mac(l.get("Port ID"))
    ip = l.get("Management address") or c.get("Address") or ""
    return name, kind, about.splitlines()[0] if about else "", mac, ip


def normalize_ports(config, statuses=None, neighbors=None):
    """Every port of one switch: the bySwitch config item, plus the matching
    status and topology items when Meraki gave them."""
    status = {_text(p.get("portId")): p for p in (statuses or {}).get("ports") or [] if isinstance(p, dict)}
    seen = {_text(p.get("portId")): p for p in (neighbors or {}).get("ports") or [] if isinstance(p, dict)}
    rows = []
    for port in config.get("ports") or []:
        if not isinstance(port, dict):
            continue
        port_id = _text(port.get("portId"))
        state = status.get(port_id, {})
        name, kind, model, mac, ip = neighbor(seen.get(port_id, {}).get("lldp"), seen.get(port_id, {}).get("cdp"))
        mode = _text(port.get("type")).capitalize()
        problems = [p for p in (state.get("errors") or []) + (state.get("warnings") or []) if p != "Port disconnected"]
        rows.append({
            "switch_serial": _text(config.get("serial")), "switch_name": _text(config.get("name")),
            "site": _text((config.get("network") or {}).get("name")), "port_id": port_id,
            "number": int(port_id) if port_id.isdigit() else 0, "name": _text(port.get("name")),
            "enabled": bool(port.get("enabled", True)), "mode": mode, "vlan": _text(port.get("vlan")),
            "voice_vlan": _text(port.get("voiceVlan")),
            "allowed_vlans": _text(port.get("allowedVlans")) if mode == "Trunk" else "",
            "poe": bool(port.get("poeEnabled")), "stp_guard": _text(port.get("stpGuard")).capitalize(),
            "access_policy": _text(port.get("accessPolicyType")),
            "link": "Disabled" if port.get("enabled") is False else _text(state.get("status")),
            "speed": _text(state.get("speed")), "uplink": bool(state.get("isUplink")),
            "poe_in_use": bool((state.get("poe") or {}).get("isAllocated")),
            "problems": "; ".join(_text(p) for p in problems),
            "neighbor": name, "neighbor_kind": kind, "neighbor_model": model, "neighbor_mac": mac, "neighbor_ip": ip,
        })
    return rows
