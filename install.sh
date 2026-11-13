#!/usr/bin/env bash
#
# Atlas installer.
#
# Run from the folder you cloned Atlas into, on Ubuntu Server:
#
#     sudo ./install.sh
#
# It installs what Atlas needs (Docker Engine and the Compose plugin, from
# Docker's own package repository), creates .env with fresh secrets, builds
# and starts Atlas, and helps you create the first administrator.
#
# It is safe to run again. It never replaces a secret that is already set,
# never removes software, and never touches your data. Running it again after
# `git pull` is also how you update Atlas.

set -euo pipefail

ATLAS_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ENV_FILE="$ATLAS_DIR/.env"
ENV_EXAMPLE="$ATLAS_DIR/.env.example"
MIN_FREE_GB=4
EXTRA_HOSTS=()
CHECK_ONLY=0

# ── Output ─────────────────────────────────────────────────────────────────

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

step() { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
ok()   { printf '  %sok%s    %s\n' "$GREEN" "$RESET" "$1"; }
note() { printf '  %snote%s  %s\n' "$YELLOW" "$RESET" "$1"; }
die()  { printf '\n%sStopped:%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

FAILURES=()
fail() { FAILURES+=("$1"); printf '  %sFAIL%s  %s\n' "$RED" "$RESET" "$1"; }

usage() {
  cat <<EOF
Usage: sudo ./install.sh [options]

  --host NAME   Also accept this name or address in the browser, for example
                atlas.example.org. Repeat for more than one. The server's own
                hostname and IP addresses are added automatically.
  --check       Only check that this server is ready. Changes nothing.
  -h, --help    Show this help.
EOF
}

# ── .env helpers ───────────────────────────────────────────────────────────

# Print the value of KEY in .env (empty if unset or missing).
env_get() {
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

# Set KEY=VALUE in .env, replacing the line if it exists.
env_set() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # "|" never appears in the values this script writes.
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# A random string of letters and digits, safe to put in .env unquoted.
random_secret() {
  local length="$1" out=""
  while (( ${#out} < length )); do
    out+="$(head -c 256 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9')"
  done
  printf '%s' "${out:0:length}"
}

# Names declared more than once in .env. Two lines for one key is how a
# setting ends up being something other than what you think it is.
env_duplicate_keys() {
  grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" | cut -d= -f1 | sort | uniq -d
}

# This server's names and IPv4 addresses, leaving out Docker's own networks.
detect_hosts() {
  local hosts=("localhost" "127.0.0.1") name
  name="$(hostname 2>/dev/null || true)";       [[ -n "$name" ]] && hosts+=("$name")
  name="$(hostname -f 2>/dev/null || true)";    [[ -n "$name" ]] && hosts+=("$name")
  if command -v ip >/dev/null 2>&1; then
    while read -r addr; do
      [[ -n "$addr" ]] && hosts+=("$addr")
    done < <(ip -4 -o addr show scope global 2>/dev/null \
               | awk '$2 !~ /^(docker|br-|veth)/ {print $4}' | cut -d/ -f1)
  fi
  hosts+=("${EXTRA_HOSTS[@]}")
  # Unique, order kept, comma separated.
  printf '%s\n' "${hosts[@]}" | awk 'NF && !seen[$0]++' | paste -sd, -
}

# Add any hosts that are missing from DJANGO_ALLOWED_HOSTS; keep what is there.
merge_allowed_hosts() {
  local current wanted
  current="$(env_get DJANGO_ALLOWED_HOSTS)"
  wanted="$(detect_hosts)"
  printf '%s,%s\n' "$current" "$wanted" | tr ',' '\n' | awk 'NF && !seen[$0]++' | paste -sd, -
}

prepare_env() {
  step "Settings file (.env)"

  if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "$ENV_EXAMPLE" ]] || die ".env.example is missing. Is this a complete copy of Atlas?"
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    ok "Created .env from .env.example"
  else
    ok ".env already exists; keeping it"
  fi

  # A file that does not end in a newline swallows the next line appended.
  [[ -z "$(tail -c 1 "$ENV_FILE")" ]] || printf '\n' >> "$ENV_FILE"

  local dupes
  dupes="$(env_duplicate_keys)"
  if [[ -n "$dupes" ]]; then
    die ".env sets these more than once: $(echo "$dupes" | paste -sd' ' -). Remove the extra lines and run this again."
  fi

  if [[ -z "$(env_get DJANGO_SECRET_KEY)" ]]; then
    env_set DJANGO_SECRET_KEY "$(random_secret 64)"
    ok "Generated DJANGO_SECRET_KEY"
  else
    ok "DJANGO_SECRET_KEY already set; not changed"
  fi

  # Losing this key means every saved API token has to be typed in again,
  # so an existing one is never replaced.
  if [[ -z "$(env_get ATLAS_ENCRYPTION_KEY)" ]]; then
    env_set ATLAS_ENCRYPTION_KEY "$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n')"
    ok "Generated ATLAS_ENCRYPTION_KEY"
  else
    ok "ATLAS_ENCRYPTION_KEY already set; not changed"
  fi

  # Postgres only reads its password the first time the database is created,
  # so an existing one must never be replaced.
  if [[ -z "$(env_get POSTGRES_PASSWORD)" ]]; then
    env_set POSTGRES_PASSWORD "$(random_secret 32)"
    ok "Generated POSTGRES_PASSWORD"
  else
    ok "POSTGRES_PASSWORD already set; not changed"
  fi

  [[ -n "$(env_get POSTGRES_DB)" ]]   || env_set POSTGRES_DB atlas
  [[ -n "$(env_get POSTGRES_USER)" ]] || env_set POSTGRES_USER atlas
  [[ -n "$(env_get ATLAS_PORT)" ]]    || env_set ATLAS_PORT 8000

  local hosts
  hosts="$(merge_allowed_hosts)"
  env_set DJANGO_ALLOWED_HOSTS "$hosts"
  ok "Atlas will answer to: ${hosts//,/, }"

  chmod 600 "$ENV_FILE"
  # Leave the file owned by whoever cloned the repo, not root.
  if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
    chown "$SUDO_UID:$SUDO_GID" "$ENV_FILE"
  fi
  ok ".env is readable only by its owner"
}

# ── Checks ─────────────────────────────────────────────────────────────────

docker_ready() {
  command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
}

atlas_running() {
  docker_ready && [[ -n "$(cd "$ATLAS_DIR" && docker compose ps -q web 2>/dev/null)" ]]
}

preflight() {
  step "Checking this server"

  if [[ $EUID -eq 0 ]]; then ok "Running as root"; else fail "Run this with sudo: sudo ./install.sh"; fi

  local id="" version="" codename=""
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    id="$(. /etc/os-release && echo "${ID:-}")"
    # shellcheck disable=SC1091
    version="$(. /etc/os-release && echo "${VERSION_ID:-}")"
    # shellcheck disable=SC1091
    codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}")"
  fi
  OS_ID="$id"; OS_CODENAME="$codename"
  case "$id" in
    ubuntu)
      if [[ "${version%%.*}" -ge 22 ]] 2>/dev/null; then ok "Ubuntu $version ($codename)"
      else fail "Ubuntu $version is too old. Atlas needs Ubuntu 22.04 or newer."; fi ;;
    debian) ok "Debian $version ($codename)" ;;
    *)
      if docker_ready; then note "Untested system '${id:-unknown}', but Docker is already here, so carrying on"
      else fail "This installer sets up Docker on Ubuntu or Debian only (found '${id:-unknown}'). Install Docker Engine and the Compose plugin yourself, then run this again."; fi ;;
  esac

  case "$(uname -m)" in
    x86_64|aarch64|arm64) ok "Processor type $(uname -m)" ;;
    *) fail "Processor type $(uname -m) is not supported." ;;
  esac

  if [[ -f "$ATLAS_DIR/compose.yaml" && -f "$ENV_EXAMPLE" ]]; then ok "Atlas files found in $ATLAS_DIR"
  else fail "compose.yaml or .env.example is missing from $ATLAS_DIR. Clone the full repository."; fi

  local free_gb
  free_gb="$(df -BG --output=avail "$ATLAS_DIR" | tail -n 1 | tr -dc '0-9')"
  if (( free_gb >= MIN_FREE_GB )); then ok "${free_gb} GB of disk free"
  else fail "Only ${free_gb} GB of disk free; Atlas needs at least ${MIN_FREE_GB} GB."; fi

  if docker_ready; then
    ok "Docker and the Compose plugin are already installed"
  else
    # Ubuntu's own Docker packages cannot sit beside Docker's. Say so now
    # instead of failing halfway through an install.
    local pkg conflicts=()
    if command -v dpkg >/dev/null 2>&1; then
      for pkg in docker.io docker-compose docker-compose-v2 docker-doc podman-docker; do
        if dpkg -s "$pkg" >/dev/null 2>&1; then conflicts+=("$pkg"); fi
      done
    fi
    if (( ${#conflicts[@]} )); then
      fail "These packages conflict with Docker's own: ${conflicts[*]}. Remove them (sudo apt remove ${conflicts[*]}) and run this again. This installer does not remove software."
    else
      note "Docker is not fully installed yet; this will install it"
    fi
  fi

  local port
  port="$(env_get ATLAS_PORT)"; port="${port:-8000}"
  if atlas_running; then
    ok "Atlas is already running here; this run will update it"
  elif command -v ss >/dev/null 2>&1 && ss -ltnH "sport = :$port" 2>/dev/null | grep -q .; then
    fail "Port $port is already in use by something else. Set ATLAS_PORT in .env to a free port."
  else
    ok "Port $port is free"
  fi

  if command -v timedatectl >/dev/null 2>&1 \
     && [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" == "no" ]]; then
    note "The clock is not synchronized. Sign-ins to outside services can fail when the time is wrong."
  fi

  if (( ${#FAILURES[@]} )); then
    printf '\n%s%d problem(s) to fix before Atlas can be installed:%s\n' "$RED" "${#FAILURES[@]}" "$RESET" >&2
    local f; for f in "${FAILURES[@]}"; do printf '  - %s\n' "$f" >&2; done
    exit 1
  fi
}

# ── Docker ─────────────────────────────────────────────────────────────────

install_docker() {
  step "Docker"

  if ! command -v curl >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl >/dev/null
  fi

  if docker_ready; then
    ok "$(docker --version)"
    ok "$(docker compose version)"
  else
    export DEBIAN_FRONTEND=noninteractive
    note "Installing Docker from download.docker.com. This takes a minute or two."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl >/dev/null

    if ! curl -fsS --max-time 15 -o /dev/null "https://download.docker.com/linux/$OS_ID/gpg"; then
      die "Cannot reach download.docker.com. Check that this server can get out to the internet (a web filter or firewall is the usual cause)."
    fi

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$OS_ID/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    if [[ ! -f /etc/apt/sources.list.d/docker.sources && ! -f /etc/apt/sources.list.d/docker.list ]]; then
      cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$OS_ID
Suites: $OS_CODENAME
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    fi

    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
    docker_ready || die "Docker was installed but 'docker compose' still does not work. Run 'docker compose version' to see why."
    ok "$(docker --version)"
    ok "$(docker compose version)"
  fi

  # Keep container logs from filling the disk. Only when there is no existing
  # configuration to disturb.
  if [[ ! -e /etc/docker/daemon.json ]]; then
    mkdir -p /etc/docker
    cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
    systemctl restart docker
    ok "Container logs limited to 30 MB each"
  else
    note "/etc/docker/daemon.json already exists; left as it is"
  fi

  systemctl enable --now docker >/dev/null 2>&1 || true
  if systemctl is-active --quiet docker; then ok "Docker is running and starts at boot"
  else die "Docker is installed but not running. See: sudo systemctl status docker"; fi
}

# ── Atlas ──────────────────────────────────────────────────────────────────

start_atlas() {
  step "Building and starting Atlas"
  cd "$ATLAS_DIR"
  docker compose up -d --build

  local port url tries=0
  port="$(env_get ATLAS_PORT)"; port="${port:-8000}"
  url="http://127.0.0.1:${port}/healthz/"
  printf '  Waiting for Atlas to answer'
  until curl -fsS --max-time 3 -o /dev/null "$url" 2>/dev/null; do
    tries=$((tries + 1))
    if (( tries > 60 )); then
      printf '\n'
      docker compose ps || true
      die "Atlas did not start within two minutes. See what it says with: sudo docker compose logs web"
    fi
    printf '.'; sleep 2
  done
  printf '\n'
  ok "Atlas is up and its database is reachable"
}

first_admin() {
  step "First administrator"
  cd "$ATLAS_DIR"

  local count
  count="$(docker compose exec -T web python -c \
    "import django; django.setup(); from django.contrib.auth import get_user_model as g; print(g().objects.filter(is_superuser=True).count())" \
    2>/dev/null | tail -n 1 | tr -dc '0-9')"

  if [[ "${count:-0}" -gt 0 ]]; then
    ok "An administrator already exists"
  elif [[ -t 0 && -t 1 ]]; then
    note "Create the account you will sign in with."
    docker compose exec web python manage.py createsuperuser \
      || note "No account created. Run this when ready: sudo docker compose exec web python manage.py createsuperuser"
  else
    note "No administrator yet. Create one with: sudo docker compose exec web python manage.py createsuperuser"
  fi
}

finish() {
  local port hosts host
  port="$(env_get ATLAS_PORT)"; port="${port:-8000}"
  hosts="$(env_get DJANGO_ALLOWED_HOSTS)"

  printf '\n%sAtlas is installed.%s Open it in a browser at:\n\n' "$BOLD$GREEN" "$RESET"
  while read -r host; do
    case "$host" in localhost|127.0.0.1|"") continue ;; esac
    printf '    http://%s:%s\n' "$host" "$port"
  done < <(printf '%s\n' "$hosts" | tr ',' '\n')

  cat <<EOF

To update later:   cd $ATLAS_DIR && git pull && sudo ./install.sh
To see the logs:   sudo docker compose logs -f web
Reports sync log:  sudo docker compose logs -f sync
To stop Atlas:     sudo docker compose down     (your data is kept)

Atlas is served over plain HTTP. Keep it on a trusted network until HTTPS
is added.
EOF
}

main() {
  while (( $# )); do
    case "$1" in
      --host)   [[ $# -ge 2 ]] || die "--host needs a name or address after it."; EXTRA_HOSTS+=("$2"); shift 2 ;;
      --host=*) EXTRA_HOSTS+=("${1#--host=}"); shift ;;
      --check)  CHECK_ONLY=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) usage >&2; die "Unknown option: $1" ;;
    esac
  done

  local h
  for h in "${EXTRA_HOSTS[@]}"; do
    [[ "$h" =~ ^[A-Za-z0-9._-]+$ ]] || die "'$h' is not a host name or IPv4 address. Leave out http:// and any port."
  done

  printf '%sAtlas installer%s  (%s)\n' "$BOLD" "$RESET" "$ATLAS_DIR"
  preflight
  if (( CHECK_ONLY )); then
    printf '\n%sThis server is ready for Atlas.%s Nothing was changed.\n' "$GREEN" "$RESET"
    exit 0
  fi
  install_docker
  prepare_env
  start_atlas
  first_admin
  finish
}

# Run only when executed, so the functions above can be tested on their own.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
