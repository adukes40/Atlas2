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
