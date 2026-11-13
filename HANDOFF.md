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

- Google, Mist and Meraki against the real systems. Field names were checked
  against live Google and Mist answers on 2026-11-13; Meraki's client search
  shape is from its API docs.

## Server

Atlas runs in an LXC container as root at /opt/atlas, so commands need no sudo.

## Next, in rough order

1. Connect Google, Mist and Meraki on the live site and check a few lookups.
2. Possibly: look up a device Incident IQ does not have by asking Google
   directly, and Mist wired clients for docked devices.
3. Users screen, so accounts are not made with `createsuperuser`.
4. Reports from a nightly sync (the Reports page says this is coming).
5. HTTPS in front of Atlas before use outside a trusted network.
