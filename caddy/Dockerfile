# Caddy with the IONOS DNS plugin, so it can prove it owns the HTTPS name by
# adding a TXT record. Nothing on the internet has to reach this server.
FROM caddy:2.11-builder AS build
RUN xcaddy build --with github.com/caddy-dns/ionos

FROM caddy:2.11
COPY --from=build /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
