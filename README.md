# Atlas

Look up a device by asset tag or serial number and see, on one screen, who it
belongs to, what state it is in, and where it was last seen. Atlas pulls from
Incident IQ, Google Workspace, Mist and Meraki.

Working today: sign-in, themes, and Incident IQ. A lookup shows the device,
who it is assigned to, any balance owed, and open tickets. Google Workspace,
Mist and Meraki come next.

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

## HTTPS

Atlas can serve itself over HTTPS with a free, publicly trusted Let's Encrypt
certificate, so browsers show no warning. The certificate is proven through
DNS (a temporary TXT record), so the server never has to be reachable from
the internet. You need:

- A name under a domain whose DNS you control, for example
  `atlas.example.org`. Today the built-in DNS host is IONOS. Others work by
  swapping the plugin in `caddy/Dockerfile` and the `dns` line in
  `caddy/Caddyfile` for one from https://github.com/caddy-dns.
- An API key from that DNS host (for IONOS: the public prefix and secret,
  joined by a dot).
- A DNS A record pointing the name at the server's internal IP address.

Then run `sudo ./install.sh --https atlas.example.org`. It asks for the API
key once, starts Caddy on ports 80 and 443, and waits for the certificate.
Caddy renews it by itself. After that, Atlas's own port answers only on the
server itself, and `sudo ./install.sh --https off` goes back to plain HTTP.

## Connect Incident IQ

Sign in as an administrator, open Settings, choose Incident IQ, and enter your
site address and an API token. "Save and test" tells you straight away whether
it worked. The token is stored encrypted and is never shown again. Atlas only
reads from Incident IQ.

## ClassLink sign-in (SAML)

Staff can sign in with ClassLink instead of an Atlas password. ClassLink never
has to reach the server: every step passes through the person's browser, so
this works on a private network. Use HTTPS first, because ClassLink's
addresses for Atlas are built from the HTTPS name.

1. In Atlas, open Settings, then ClassLink sign-in. Note the Entity ID and
   ACS URL, or download Atlas's metadata.
2. In the ClassLink Management Console, open Single Sign-On, SAML Console,
   and add a new app. Paste Atlas's metadata (or the Entity ID and ACS URL),
   and map the attributes `email` to Email, `firstName` to Given Name and
   `lastName` to Family Name. Assign the app to the staff who should use Atlas.
3. Copy ClassLink's IdP metadata (the XML, or its metadata URL) back into
   Atlas, tick "Turn on ClassLink sign-in" and save.

People are matched to existing Atlas users by email. With "Create accounts
for new people" on, anyone else ClassLink signs in gets a Technician account;
make someone an Administrator on the Users screen. Password sign-in always
stays available, so keep at least one administrator with a password.

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

Graphite is the default look and opens in dark mode. Slate is the
other built-in theme, chosen in Settings → Organization. Anyone can
switch between Light, Dark and Auto for themselves. A theme is one CSS file in
`app/core/static/core/themes/` plus an entry in `app/core/themes.py`.

## Not done yet

- Keep a copy of `.env` somewhere safe. It holds `ATLAS_ENCRYPTION_KEY`; without
  it the saved API tokens cannot be read and have to be entered again.
- Atlas is served over plain HTTP. Put it behind HTTPS before it is used
  outside a trusted network, because sign-in passwords travel unencrypted.
- Further users are created with `createsuperuser` until the Users screen exists.
