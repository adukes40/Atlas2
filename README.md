# Atlas

Look up a device by asset tag or serial number and see, on one screen, who it
belongs to, what state it is in, and where it was last seen. Atlas pulls from
Incident IQ, Google Workspace, Mist and Meraki.

This is milestone 1: the signed-in shell, themes, and light/dark mode. No
vendor connections yet.

## Install

On a fresh Ubuntu Server with Docker and the Compose plugin:

```
git clone <this repo> /opt/atlas
cd /opt/atlas
cp .env.example .env
nano .env            # set DJANGO_SECRET_KEY, POSTGRES_PASSWORD, DJANGO_ALLOWED_HOSTS
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Then open `http://<server>:8000`.

## Update

```
cd /opt/atlas
git pull
docker compose up -d --build
```

Database changes are applied automatically when the `web` container starts.

## Develop and test without Docker

```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd app
DJANGO_DEBUG=1 python manage.py migrate
DJANGO_DEBUG=1 python manage.py test
DJANGO_DEBUG=1 python manage.py runserver
```

Without `POSTGRES_HOST` set, Atlas uses a local SQLite file. That is for
development only.

## Themes

The look is chosen in Settings → Organization. A theme is one CSS file in
`app/core/static/core/themes/` plus an entry in `app/core/themes.py`.

## Not done yet

- Atlas is served over plain HTTP. Put it behind HTTPS before it is used
  outside a trusted network, because sign-in passwords travel unencrypted.
- Users are created with `createsuperuser` until the Users screen exists.
