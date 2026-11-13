# Atlas: notes for Claude

Atlas is a Django 5.2 app for a school district's IT team. Look up one device
by asset tag or serial and see who has it, its state, and where it was last
seen, pulled from Incident IQ, Google Workspace, Mist and Meraki. Read
`HANDOFF.md` first for where the work stands and what comes next.

## Layout

- `app/atlas/` project settings and root URLs (routes live in `app/core/urls.py`, which includes `reports/urls.py`)
- `app/core/` sign-in shell, themes, Organization (single row), Settings, Users
  (Django's own User; superuser means Administrator), System, `/healthz/`
- `app/devices/` Device 360 page, lookup service, open-lookup tabs, recent lookups, `LookupLog`
- `app/reports/` Reports: nightly copy (`sync.py`, run by the `sync` compose
  service via `manage.py run_sync_schedule`), every report and filter field in
  `catalog.py`, `Device`/`Person` rebuilt whole each sync, `Sighting` kept
- `app/integrations/` one config model per outside system (always row 1), encrypted tokens
  (`crypto.py`, key from `ATLAS_ENCRYPTION_KEY`), sidebar status (`status.py`)
- `app/integrations/incidentiq/` `client.py` is the only code that talks to Incident IQ;
  `normalize.py` is the only code that reads its raw JSON
- `app/integrations/google/`, `mist/`, `meraki/` the same split for the other three.
  They share `http.py` (requests and plain error messages), `errors.py`
  (`IntegrationError`), `network.py` (`Sighting`, MAC helper) and the abstract
  `Connection` model. `devices/service.py` asks Google by serial, then Mist and
  Meraki by Google's MACs. Incident IQ open tickets are found by the assigned
  person (the asset's own count is always 0), and so are fees (the person's
  Fee Tracker field via `/users/{id}`, never the asset's). `LookupResult.conflicts` holds the Incident IQ vs Google checks.
  `_add_connection` follows Meraki's switch record to the switch port (Connection card).

## Conventions

- Each outside system gets a client module plus a normalize module, following Incident IQ.
  Errors are exception subclasses whose message is written to be shown to a person.
- Tokens are never logged, returned to a browser, or written back into a form.
- Tests run against stand-ins (`app/integrations/testing.py`), never a real vendor.
  `incident_iq(routes, others=...)` also answers Google, Mist and Meraki by full URL.
- Settings pages are superuser only and go through `views._save_and_test`. Atlas only reads from vendors.
- A theme is one CSS file in `app/core/static/core/themes/` plus an entry in `app/core/themes.py`.
- User-facing text is plain language. Commit messages describe the change for an IT admin.

## Commands

```
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cd app && DJANGO_DEBUG=1 python manage.py test
```

Without `POSTGRES_HOST`, Atlas uses SQLite. Production is Docker Compose
(`install.sh`), which runs migrations on start.

Keep `HANDOFF.md` and this file current at the end of each piece of work.
