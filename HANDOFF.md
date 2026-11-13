# Handoff

Last updated 2026-11-13 (Reports).

## Done

1. Milestone 1: signed-in shell, Device 360 / Reports / Settings / System
   screens, themes (Graphite default, Slate), light/dark/auto.
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

12. Reports and the nightly sync (2026-11-13). Layout "A with B's tiles",
    picked by Austin from four mockups: an Overview of count tiles (with a
    30-day trend once nights accumulate), then a library of 15 reports (14 after "Never on our network" was dropped), each
    one table with filter chips on any field, Columns, Group by, Save as and
    Export CSV. Column headers and group headings carry the accent colour
    (Austin asked for them to stand out). Technicians see every report.
    - `app/reports/`: `sync.py` copies Google (every Chromebook), Incident IQ
      (every asset, every person with Fee Tracker balance) and Mist (Wi-Fi
      clients seen in the last day, Chromebook MACs only) and rebuilds the
      `Device` and `Person` tables in one transaction. Google or Incident IQ
      failing keeps yesterday's copy; Mist failing rebuilds with the
      `Sighting` rows already kept (they accumulate, so "last on our network"
      reaches back past one day). Requests retry on 429, timeouts and 5xx
      (30 s, doubling, 3 times; `integrations/http.with_retries`).
    - `catalog.py` holds every field, filter operation and report. A report is
      only starting filters, so adding a report is a few lines there.
    - The `sync` service in compose.yaml runs `manage.py run_sync_schedule`:
      2:00 AM America/New_York (`ATLAS_SYNC_HOUR`, `ATLAS_TIME_ZONE`), plus
      "Sync now" from Reports or System, checked each minute. It also runs
      right after first start, since that day's 2 AM run is missing.
      `manage.py sync` runs one by hand. Runs are listed on System.
    - With 28,000 made-up devices and 14,000 people, the rebuild took 5 s on
      SQLite and every report page loaded in under 150 ms.
    138 tests.
    Inventory mismatches was dropped as a report the same day (Austin: "those
    can go away"). The `mismatch` field is still worked out each sync, so it
    can be filtered on from any device report or brought back as a report.
13. Network gear (2026-11-13). Layout "A plus B", picked by Austin from three
    mockups: a Network gear group of 9 reports (Access points, Access points
    offline, Switches, Switch ports, Devices on switch ports, Ports with
    errors, Firmware versions, Wireless networks, Personal Wi-Fi keys), plus a
    switch page (`reports/switch/<serial>/`) drawn like the faceplate, odd
    ports on top, with the chosen port's settings and what is plugged in.
    Switch names in the reports link to it.
    - The nightly sync adds two steps. "Meraki": networks, device
      availabilities, devices (switches and MR APs), and per-switch port
      config, port status and LLDP/CDP neighbors, all org-wide lists paged by
      Meraki's Link header (`get_json(link=True)`, `MerakiClient.org_list`),
      about 35 calls. "Mist access points": sites, `/stats/devices?type=ap`,
      wlans and psks (`MistClient.org_list`), about 5 calls. Each step replaces
      only its own rows (`NetDevice` by vendor, `SwitchPort`, `Wlan`, `Psk`),
      so a failed system keeps yesterday's rows and Chromebook reports still
      rebuild.
    - `link_gear()` puts each AP on its switch port: the Meraki port whose
      LLDP chassis MAC is the AP's MAC, else the switch and port the Mist AP
      reports itself (`lldp_stat`, for non-Meraki switches). The port's
      neighbor name is then set to the AP's name (Meraki often only has its MAC).
    - PSK passphrases are never read into Atlas; only name, SSID, role, VLAN
      and expiry.
    - Meraki MR APs are included (Austin had not said whether they are
      retiring); filter Vendor to drop them.
    - Filter chips open for editing: click one to change its operation or
      value in place (`replace` on the add-filter request), no remove and re-add.
      "Never on our network" was dropped (Austin, same day). Pop-up menus
      (filters, Columns, Sync now) close on a click outside or Esc
      (`core/static/core/atlas.js`, the only script in Atlas). Light/Dark is one
      sun or moon button that switches to the other mode (`mode_next`; on Auto,
      atlas.js picks the opposite of the device).
    - Each platform syncs on its own (Austin: "should not need to run full
      scans on everything just to update one platform"). Sync now, on Reports
      and System, opens a menu: tick Google, Incident IQ, Mist or Meraki, or
      Sync everything. `SyncRun.systems` holds the choice (blank is all, as
      the nightly run is). A platform not asked for is rebuilt from the copy
      as it stands (`sync.KEEP`, `_iiq_from_copy`, `_google_from_copy`), so a
      Google-only run makes no Incident IQ calls and keeps assignments. A
      second request joins one still waiting. "Data as of" is now the oldest
      platform's copy (`sync.as_of()`), and the menu shows each one's time.
    - WAN IP setting (Settings, Organization): one per line, an address, the
      start of one (`198.51.100` matches `198.51.100.x`, not `198.51.1000`),
      or a range like `192.0.2.0/24`. `Organization.on_network(ip)`. Device 360
      tags Google's last WAN IP "On our network" or "Off network". The sync
      keeps Google's WAN IP (`Device.wan_ip`) and works out `off_network` on
      every rebuild, so after changing the setting, any Sync now updates it.
      Filter fields "Last WAN IP" and "Off our network (by WAN IP)". 147 tests.

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

- Reports sync against the live systems. Incident IQ paging comes from the
  original ATLAS's notes (assets: POST body Paging; people: GET
  `/users?$p=&$s=`), page size 500 asked for; the sync follows TotalRows if
  Incident IQ caps the page smaller. The people list's field names
  (FullName, RoleName or Role.Name, LocationName or Location.Name, IsActive,
  CustomFieldValues for fees) have fallbacks but are unchecked. Status words
  in "Lost but still online" (lost, stolen, missing, retired, dispos), role
  "Student", and "student" OU / "staff" policy in "Wrong network" are
  guesses the chips let anyone change.
- Meraki Wi-Fi clients are not in the nightly copy: Chromebooks are on Mist
  Wi-Fi. Meraki's switches, ports and APs are (item 13).
- Network gear against the live systems. Meraki's port config, status and
  LLDP shapes and Mist's AP, wlan shapes were read live on 2026-11-13; Mist's
  `num_clients` and `lldp_stat` in the org device stats, and the PSK list
  fields, are from the API docs. Meraki per-port PoE watts are not in the
  org-wide status list, so PoE draw shows only for Mist APs.

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
4. Run the first sync on the live site (System, Sync now, or
   `docker compose exec sync python manage.py sync`) and check its counts.
5. HTTPS in front of Atlas before use outside a trusted network.
