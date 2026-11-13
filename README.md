# Atlas

Look up a device by asset tag or serial number and see, on one screen, who it
belongs to, what state it is in, and where it was last seen. Atlas pulls from
Incident IQ, Google Workspace, Mist and Meraki.

This is milestone 1: the signed-in shell, themes, and light/dark mode. No
vendor connections yet.

## Install

On a fresh Ubuntu Server (22.04 or newer):

```
sudo apt install -y git
git clone <this repo> /opt/atlas
cd /opt/atlas
sudo ./install.sh
```

The installer:

1. Checks the server and lists everything that needs fixing in one go.
2. Installs Docker Engine and the Compose plugin from Docker's own package
   repository, if they are not already there.
3. Creates `.env` with a fresh secret key and database password, and adds
   the server's hostname and IP addresses to the names Atlas answers to.
4. Builds and starts Atlas and its database.
5. Asks you to create the first administrator.
6. Prints the address to open in a browser.

To reach Atlas by a DNS name, pass it in: `sudo ./install.sh --host atlas.example.org`.
To only check the server without changing anything: `sudo ./install.sh --check`.

It is safe to run again. It never replaces a secret that is already set,
never removes software, and never touches your data.

## Update

```
cd /opt/atlas
git pull
sudo ./install.sh
```

Database changes are applied automatically when Atlas starts.

## Day to day

```
sudo docker compose logs -f web      # watch the logs
sudo docker compose down             # stop (data is kept)
sudo docker compose up -d            # start
sudo docker compose exec web python manage.py createsuperuser   # add an administrator
```

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

Graphite is the default look and opens in dark mode. Slate and Caesar Rodney
are the other built-in themes, chosen in Settings → Organization. Anyone can
switch between Light, Dark and Auto for themselves. A theme is one CSS file in
`app/core/static/core/themes/` plus an entry in `app/core/themes.py`.

## Not done yet

- Atlas is served over plain HTTP. Put it behind HTTPS before it is used
  outside a trusted network, because sign-in passwords travel unencrypted.
- Further users are created with `createsuperuser` until the Users screen exists.
