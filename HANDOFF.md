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

## Not confirmed

- Whether the Site ID / Product ID change fixed the connection on the
  district's live Incident IQ site.

## Next, in rough order

1. Milestone 3: Google Workspace. Chromebook by serial from the Admin SDK
   (status, OU, last sync, recent users, MAC addresses) into the Device 360
   status strip and Activity card. Needs a service account with domain-wide
   delegation; follow the Incident IQ client/normalize/settings pattern.
2. Milestone 4: Mist and Meraki. Find the client by MAC for Location and Last
   seen and the Network card.
3. Users screen, so accounts are not made with `createsuperuser`.
4. Reports from a nightly sync (the Reports page says this is coming).
5. HTTPS in front of Atlas before use outside a trusted network.
