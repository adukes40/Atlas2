# Handoff

Last updated 2026-11-13.

## Done

1. Milestone 1: signed-in shell, Device 360 / Reports / Settings / System
   screens, themes (Graphite default, Slate, Caesar Rodney), light/dark/auto.
2. `install.sh`: one command to install or update on Ubuntu with Docker Compose.
3. Milestone 2: Incident IQ lookups. Device, assigned person, fees owed, open
   tickets, open-lookup tabs, recent lookups, lookup log.
4. Optional Incident IQ Site ID and Product ID headers for sites that reject
   token-only calls (the combination the original ATLAS used). 69 tests pass.

5. Gunicorn uses threaded workers so idle browser connections no longer time
   out workers, and its control socket is off (it logged a permission error).
   Incident IQ Save and test confirmed working on the live site 2026-11-13.
6. Milestones 3 and 4: Google Workspace, Mist and Meraki, each with a Settings
   page and Save and test. A lookup that finds one device in Incident IQ then
   asks Google for the Chromebook by serial (status, OU, last sync, ChromeOS,
   updates-until, last IP, MACs, five most recent users), then asks Mist
   (wireless clients, last 7 days) and Meraki (org client search) by those
   MACs. The most recent sighting fills Location and Last seen. A failure in
   any of the three shows on its card and in Settings, never sinks the lookup.
   97 tests pass.
7. Users screen (Settings, Users): administrators add people, change names,
   email and role (Technician or Administrator), reset passwords, and turn an
   account off instead of deleting it, so lookup history stays. Nobody can turn
   off their own account or remove their own administrator access, so there is
   always one administrator left. Passwords go through Django's validators.
   105 tests pass.

8. Device 360 regrouped (option E, picked by Austin from five mockups on
   2026-11-13). A header card with the device, links straight to it in
   Incident IQ and Google Admin, Refresh, and a strip of Last seen, Google,
   Balance and Tickets. Then a warning when Incident IQ and Google disagree,
   and three cards: Who has it (person, link to them in Incident IQ, recent
   Google sign-ins), Where it is (Mist or Meraki sighting, LAN and WAN IP,
   MACs), What state it's in (ChromeOS, OU, update date with Supported /
   Ending soon / Expired, boot mode, Google's asset ID, memory and storage
   use). Fees owed (each Fee Tracker charge) and open tickets show below
   when there are any. Open tickets are a new Incident IQ call, made only
   for the assigned person. Disagreement checks are the original ATLAS's
   (tag equals serial, last Google sign-in is not the assigned person) plus
   Google's asset ID against the Incident IQ tag. 109 tests pass.

9. Connection card (option 1 path, picked 2026-11-13). Meraki lists a
   Wi-Fi client on the switch port its Mist AP plugs into (confirmed live:
   ssid null, switchport = AP uplink), so "Where it is" now leads with the
   latest Wi-Fi sighting and the Meraki switch record feeds the Connection
   card: SSID, VLAN, Mist PSK name ("policy"), Meraki client policy (group
   policy name, else device policy such as Normal), AP, switch name and port
   (switch found by `recentDeviceMac`, cached a day), and the port's mode,
   native/access VLAN, allowed VLANs, voice VLAN, PoE. 3 to 4 extra Meraki
   calls when a switch record exists. Notes in the network card now have a
   tinted background (`.note`). All switches are Meraki; APs are Mist. 113 tests.

10. Static files get hashed names in production (WhiteNoise manifest
    storage, chosen when DJANGO_DEBUG is off), so a deploy's new CSS is never
    hidden by a browser's cached copy. Debug and tests use plain names.

11. Fees come only from the assigned person (2026-11-13, Austin: "do not
    fall back.. only pull from owner"). Atlas calls `GET /users/{UserId}` and
    reads the Fee Tracker custom field there; the asset's own custom fields
    are ignored. No person, no fee record, or an Incident IQ error shows
    Nothing owed. The response shape (`{"Item": {...}}` or the bare user) is
    not yet checked against live Incident IQ. 118 tests.

## How the three new connections work

- Google: service account JSON key (stored encrypted) plus an admin email to
  act as. Atlas signs its own JWT with `cryptography` (no Google library) and
  asks only for `admin.directory.device.chromeos.readonly`. Access tokens are
  kept in memory per process for their hour.
- Mist: org API token, sent as `Authorization: Token`. The portal address
  picks the cloud (manage.gc1.mist.com becomes api.gc1.mist.com). Org ID is
  found from `/self` when the token sees one org. Site and AP names are cached
  for a day in Django's cache (per process).
- Meraki: API key as a Bearer token. Org ID is found from `/organizations` when
  the key sees one org. Meraki answers 404 for a MAC it never saw.
- Shared code: `integrations/http.py` (timeouts and HTTP errors as plain
  messages), `integrations/errors.py` (`IntegrationError`, the base of every
  system's error), `models.Connection` (abstract base for every config row),
  `views._save_and_test` and `templates/integrations/connection.html`.

## Not confirmed

- Open tickets (confirmed on the live site 2026-11-13): Incident IQ files
  tickets under the person, its `OpenTickets`/`HasOpenTickets` on the asset
  stay 0, and the `asset` and `assetid` ticket facets return nothing or
  everything. Atlas asks `POST /tickets` with the `user` facet (the assigned
  person) and keeps open tickets whose JSON names the asset. A ticket on the
  device filed for someone else is missed. `/assets/{id}/tickets` is a 404.
- Fee Tracker charge names: the field holding a charge's name is not known,
  so Atlas tries Description, Name, FeeName, FeeType, Title, Reason, else
  shows "Charge".
- Fees read from `GET /users/{id}`: untested live; both `{"Item": user}`
  and a bare user are accepted.

- Google, Mist and Meraki against the real systems. Field names were checked
  against live Google and Mist answers on 2026-11-13; Meraki's client search
  shape is from its API docs.

## Server

Atlas runs in an LXC container as root at /opt/atlas, so commands need no sudo.

## Next, in rough order

1. All four connect on the live site (2026-11-13). Look up a few in-use
   Chromebooks to confirm the Mist and Meraki answers (Meraki is from docs).
2. Check option E on the live site, especially open tickets and fee names.
   Not built yet from the original: battery wear (needs Google's telemetry
   API, a new scope) and Manage Device (Atlas only reads from vendors).
3. Possibly: look up a device Incident IQ does not have by asking Google
   directly, and Mist wired clients for docked devices.
4. Reports from a nightly sync (the Reports page says this is coming).
5. HTTPS in front of Atlas before use outside a trusted network.
