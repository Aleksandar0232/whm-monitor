#!/usr/bin/env bash
# Checks a cPanel/WHM server before `make up`. Run as root from the repo root:
#   bash scripts/preflight.sh
# Read-only except for two things: it fixes .env permissions and writes the
# detected EXIM_SPOOL_GID into .env.
set -uo pipefail
cd "$(dirname "$0")/.."

FAILURES=0
WARNINGS=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*"; }
info() { printf '  \033[36mINFO\033[0m  %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
# Read KEY=value from .env without sourcing it (values may contain $ or quotes).
getenv() { grep -E "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2-; }

echo "Docker"
if command -v docker >/dev/null 2>&1; then
    ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '(daemon not reachable)')"
else
    fail "docker is not installed"
fi
if docker compose version >/dev/null 2>&1; then
    ok "docker compose $(docker compose version --short)"
else
    fail "the docker compose v2 plugin is missing (dnf install docker-compose-plugin)"
fi
avail_kb=$(df -Pk /var/lib/docker 2>/dev/null | awk 'NR==2 {print $4}')
if [[ -n ${avail_kb:-} ]] && (( avail_kb < 5 * 1024 * 1024 )); then
    warn "less than 5 GB free for /var/lib/docker (Prometheus keeps up to PROM_RETENTION_SIZE)"
fi

echo ".env"
if [[ ! -f .env ]]; then
    fail ".env is missing - run: make init"
else
    perms=$(stat -c %a .env)
    if [[ $perms == 600 ]]; then
        ok ".env is chmod 600"
    else
        chmod 600 .env && ok ".env permissions fixed (were $perms)"
    fi
    # Every line must be KEY=value, a comment or empty. A comment split in two by
    # an accidental Enter makes `docker compose` refuse the whole file. Only line
    # numbers are printed, in case a broken line holds a secret.
    bad_lines=$(grep -nvE '^[A-Za-z_][A-Za-z0-9_]*=|^[[:space:]]*#|^[[:space:]]*$' .env | cut -d: -f1 | paste -sd, -)
    if [[ -n $bad_lines ]]; then
        fail ".env line(s) $bad_lines are not KEY=value or a comment; see them with: sed -n '${bad_lines%%,*}p' .env"
        info "remove them with: sed -i -E '/^([A-Za-z_][A-Za-z0-9_]*=|[[:space:]]*#|[[:space:]]*\$)/!d' .env"
    else
        ok ".env syntax is valid"
    fi
    for var in SERVER_NAME WHM_API_TOKEN GRAFANA_ADMIN_PASSWORD; do
        if [[ -n "$(getenv "$var")" ]]; then ok "$var is set"; else fail "$var is empty"; fi
    done
    if [[ -n "$(getenv TELEGRAM_BOT_TOKEN)" && -n "$(getenv TELEGRAM_CHAT_ID)" ]]; then
        ok "Telegram is configured"
    else
        warn "Telegram is not configured: alerts will only show in the Alertmanager UI"
    fi
    # The final word: does Compose itself accept compose.yaml together with .env?
    if [[ -z $bad_lines ]] && docker compose version >/dev/null 2>&1; then
        if compose_err=$(docker compose config --quiet 2>&1); then
            ok "docker compose accepts compose.yaml and .env"
        else
            fail "docker compose rejects the configuration: $compose_err"
        fi
    fi
fi

echo "Ports"
if docker compose ps -q 2>/dev/null | grep -q .; then
    info "the stack is already running, skipping the port check"
else
    for port in 9877 9100 9090 9093 3000; do
        # A successful connect to 127.0.0.1 means something already listens there.
        if ! (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
            ok "port $port is free"
        else
            owner=$(ss -Hltnp "sport = :$port" 2>/dev/null | grep -oE 'users:\(\("[^"]+' | cut -d'"' -f2)
            fail "port $port is taken${owner:+ by $owner}"
        fi
    done
fi

echo "Exim spool"
SPOOL=/var/spool/exim/input
if [[ -d $SPOOL ]]; then
    gid=$(stat -c %g "$SPOOL")
    mode=$(stat -c %a "$SPOOL")
    if (( (8#$mode & 8#050) == 8#050 )); then
        ok "$SPOOL is readable by group $(stat -c %G "$SPOOL") (gid $gid, mode $mode)"
    else
        warn "$SPOOL has mode $mode, its group cannot read it: the exim_queue collector will fail"
    fi
    sample=$(find "$SPOOL" -maxdepth 2 -name '*-H' -print -quit 2>/dev/null)
    if [[ -n $sample ]] && (( (8#$(stat -c %a "$sample") & 8#040) == 0 )); then
        warn "spool files are not group-readable (e.g. $sample)"
    fi
    if [[ -f .env ]]; then
        if grep -q '^EXIM_SPOOL_GID=' .env; then
            sed -i "s/^EXIM_SPOOL_GID=.*/EXIM_SPOOL_GID=$gid/" .env
        else
            echo "EXIM_SPOOL_GID=$gid" >> .env
        fi
        ok "EXIM_SPOOL_GID=$gid written to .env"
    fi
else
    warn "$SPOOL not found: remove exim_queue from COLLECTORS"
fi

echo "Firewall"
if [[ -f /etc/csf/csf.conf ]]; then
    csf_docker=$(grep -E '^DOCKER[[:space:]]*=' /etc/csf/csf.conf | cut -d'"' -f2)
    info "CSF found (DOCKER = \"${csf_docker:-0}\"). This stack uses host networking and"
    info "binds to 127.0.0.1, so it needs no firewall ports and survives csf -r."
else
    info "CSF not found"
fi

echo "WHM API"
token=$(getenv WHM_API_TOKEN)
if [[ -z $token ]]; then
    fail "skipped: WHM_API_TOKEN is empty"
else
    user=$(getenv WHM_USER); user=${user:-root}
    url=$(getenv WHM_URL); url=${url:-https://127.0.0.1:2087}
    # Header via stdin so the token never shows up in `ps`.
    resp=$(printf 'Authorization: whm %s:%s\n' "$user" "$token" \
        | curl -sk --max-time 15 -H @- "$url/json-api/version?api.version=1" 2>&1)
    version=$(grep -oE '"version"[[:space:]]*:[[:space:]]*"[^"]+"' <<<"$resp" | cut -d'"' -f4)
    if [[ -n $version ]]; then
        ok "token works, cPanel & WHM $version"
    else
        fail "WHM API call failed: $(head -c 200 <<<"$resp")"
    fi
fi

echo
if (( FAILURES > 0 )); then
    echo "$FAILURES problem(s), $WARNINGS warning(s). Fix the FAIL lines, then run this again."
    exit 1
fi
echo "Ready ($WARNINGS warning(s)). Next: make up && make check"
