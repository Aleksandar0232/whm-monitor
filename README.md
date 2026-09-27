# whm-monitor

[![CI](https://github.com/Aleksandar0232/whm-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/Aleksandar0232/whm-monitor/actions/workflows/ci.yml)

Monitoring for a **cPanel/WHM server**, running in Docker on the server itself. A custom Python exporter reads WHM's API and the Exim spool, **Prometheus** stores the metrics and evaluates alert rules, **Alertmanager** sends alerts to **Telegram**, and **Grafana** shows everything on one dashboard.

It watches what a hosting support engineer actually gets tickets about: services that went down, a mail queue full of spam, AutoSSL that silently stopped renewing, accounts at their disk quota, brute-force waves.

```
 cPanel/WHM server: every container on the host network, 127.0.0.1 only
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  WHM API :2087 ──┐                                                       │
│                  ├─► whm-exporter :9877 ─┐                               │
│  Exim spool (ro)─┘                       │ scrape every 30s              │
│                                          ├─► Prometheus :9090            │
│  /proc /sys (ro) ─► node-exporter :9100 ─┘      │        │               │
│                                                 │        │ alerts        │
│                                                 ▼        ▼               │
│                                      Grafana :3000   Alertmanager :9093  ┼─► Telegram
│                                            ▲                             │
└────────────────────────────────────────────┼─────────────────────────────┘
                                             │ SSH tunnel
                                        your laptop
```

Nothing listens on a public interface and no firewall port has to be opened.

---

## What it watches

| Area | Source | Alert (default threshold) |
|---|---|---|
| Services (httpd, exim, mysql, dovecot, …) | `servicestatus` | enabled service not running for 3 min |
| Exim queue size, frozen, bounces, oldest message, top senders | spool files | > 500 queued (warning), > 2000 (critical), > 100 frozen |
| Outgoing mail per cPanel user, last hour | `emailtrack_user_stats` | > 500 sent, > 50 % failed/deferred, hourly limit hit |
| SSL certificate expiry per vhost | `fetch_ssl_vhosts` | valid CA cert expiring in < 7 days (AutoSSL failed) |
| Disk and inode quota per account | `get_disk_usage` | > 90 % of quota |
| Accounts, suspended accounts, plans, owners | `listaccts` | - |
| IPs blocked by cPHulk | `get_cphulk_brutes`, `get_cphulk_excessive_brutes` | > 50 blocked IPs |
| cPanel & WHM version | `version` | - |
| Load, memory, disk space, inodes | node-exporter | load > 2× cores, < 10 % RAM, < 10 % disk, disk full within 24 h |

## What's in the repo

```
whm-monitor/
├── compose.yaml                  # the whole stack: 5 services, all on the host network
├── .env.example                  # settings template (token, Telegram, Grafana password)
├── Makefile                      # make init / preflight / up / check / test-alert ...
├── scripts/preflight.sh          # checks the server before the first start
├── exporter/                     # the custom Python exporter
│   ├── Dockerfile
│   ├── whm_exporter/
│   │   ├── __main__.py           # entry point: serve, --check, --once, --healthcheck
│   │   ├── config.py             # all settings from environment variables
│   │   ├── whm_client.py         # WHM API 1 client (auth, errors, retries)
│   │   ├── runner.py             # background refresh + cache that Prometheus reads
│   │   └── collectors/           # one file per data source
│   └── tests/                    # pytest suite + a fake WHM server with fixtures
├── prometheus/
│   ├── prometheus.yml            # scrape targets
│   ├── rules/whm.yml, node.yml   # alert rules
│   └── tests/rules_test.yml      # unit tests for the alert rules (promtool)
├── alertmanager/
│   ├── alertmanager.yml          # routing, grouping, inhibition, Telegram receiver
│   ├── templates/telegram.tmpl   # the Telegram message format
│   └── entrypoint.sh             # keeps the bot token out of config files
├── grafana/                      # datasource + dashboard, provisioned automatically
└── .github/workflows/ci.yml      # lint, tests, promtool/amtool checks, image build
```

---

## Step-by-step on the VPS

All commands run as `root` on the cPanel server.

### Step 1: Docker and Compose

If you set up [docker-vps-starter](https://github.com/Aleksandar0232/docker-vps-starter), you already have both. Check:

```bash
docker version --format '{{.Server.Version}}'
docker compose version
```

### Step 2: Get the code

```bash
cd /opt
git clone https://github.com/Aleksandar0232/whm-monitor.git
cd whm-monitor
```

### Step 3: Create a WHM API token

WHM › **Development** › **Manage API Tokens** › **Generate Token**. Name it `whm-monitor`, give it no expiry, and copy the token (WHM shows it once).

Privileges: the easy way is a token with all privileges. If your WHM version lets you restrict a token to IP addresses, restrict it to `127.0.0.1`. If you prefer a minimal token, start small and run `make check` in step 7: every collector that lacks a privilege shows `FAIL` with a permission error, so you know exactly what to add, or you can switch that collector off with `COLLECTORS=`.

### Step 4: Create a Telegram bot (optional, 2 minutes)

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Send any message to your new bot (or add it to a group and write in the group).
3. Get the chat id:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"chat":{"id":-\?[0-9]*'
   ```
   Group ids are negative, and that's fine.

Without Telegram the stack still works: alerts appear in the Alertmanager UI.

### Step 5: Create your settings file

```bash
make init          # creates .env (chmod 600) with a random Grafana password
nano .env          # set WHM_API_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

| Variable | Default | Meaning |
|---|---|---|
| `SERVER_NAME` | hostname | Label shown in every alert |
| `WHM_API_TOKEN` | - | Token from step 3 (required) |
| `WHM_URL` | `https://127.0.0.1:2087` | WHM address; loopback, since the exporter runs on the server |
| `WHM_VERIFY_TLS` | `false` | The WHM certificate is for the hostname, not `127.0.0.1`. Use `WHM_URL=https://<hostname>:2087` and `true` for full verification |
| `COLLECTORS` | `all` | Or a list, e.g. `services,ssl,exim_queue` |
| `EMAIL_STATS_WINDOW` | `3600` | Window for per-user mail stats, in seconds |
| `EXIM_SPOOL_GID` | `12` | Group that can read the Exim spool; set by preflight |
| `GRAFANA_ADMIN_PASSWORD` | random | Grafana `admin` password |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | empty | From step 4 |
| `PROM_RETENTION_TIME`, `PROM_RETENTION_SIZE` | `30d`, `5GB` | How much history Prometheus keeps |

Each collector's refresh interval can be changed with `INTERVAL_<NAME>` (for example `INTERVAL_SSL=1800`); add it to `.env` and to the exporter's `environment:` in `compose.yaml`.

### Step 6: Preflight

```bash
make preflight
```

It checks Docker, that ports 9877/9100/9090/9093/3000 are free, that the token works, and it finds the group that owns `/var/spool/exim/input` and writes it to `.env`. Fix every `FAIL` line before going on.

### Step 7: Start and check

```bash
make up            # builds the exporter image and starts everything
make ps            # all five should be "running" / "healthy"
make check         # runs every collector once against WHM
```

`make check` prints one line per collector. This is the output against the fake WHM from the tests; on a real server the times and series counts are higher:

```
COLLECTOR    STATUS    TIME  DETAIL
server       OK       0.00s  1 series
services     OK       0.00s  23 series
accounts     OK       0.00s  8 series
disk         OK       0.00s  10 series
ssl          OK       0.00s  9 series
cphulk       OK       0.00s  2 series
email        OK       0.00s  13 series
exim_queue   OK       0.00s  7 series
```

Any `FAIL` line tells you why (see *Troubleshooting*).

### Step 8: Open Grafana

The UIs listen only on `127.0.0.1`, so reach them through SSH from your own computer:

```bash
ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 root@your-server
```

Then open:

- **http://localhost:3000**: Grafana, user `admin`, password from `.env`. The **WHM server overview** dashboard is the home page.
- **http://localhost:9090/alerts**: Prometheus, which alerts are pending or firing.
- **http://localhost:9093**: Alertmanager, active alerts and silences.

### Step 9: Test Telegram

```bash
make test-alert
```

A message arrives within about 30 seconds:

```
🔥 vps.example.com · FIRING

WHMTestAlert · warning
Test alert from whm-monitor, Telegram works
```

---

## How it works

**Why host networking.** The exporter has to reach WHM on `127.0.0.1:2087`, and node-exporter needs the host's network stack anyway. With `network_mode: host` and every service bound to `127.0.0.1`, Docker publishes no ports and adds no NAT rules. That also means `csf -r` can't break this stack, which was a real problem with bridge networking in docker-vps-starter.

**Why collectors don't run during a scrape.** Some WHM calls take seconds on a busy server (`get_disk_usage`, `emailtrack_user_stats`). Each collector runs in its own thread on its own interval (services every 30 s, SSL every 15 min, …) and stores its last good result. A Prometheus scrape only reads those results, so it's always fast and never hammers WHM.

**Why data expires.** If WHM stops answering, the last good values are served for up to 3 intervals and then dropped. Otherwise a dead API would keep reporting "all services running" forever. `whm_collector_up`, `whm_collector_stale` and `whm_collector_last_success_timestamp_seconds` show what's happening, and the `WHMCollectorFailing` alert fires after 15 minutes.

**Why the Exim queue comes from the spool.** `exim -bpc` doesn't exist inside the container, and there's no WHM function for queue statistics. Every queued message has a `<id>-H` file whose first lines hold the envelope (Exim spec, *Format of spool files*). The collector reads only that part, never headers or bodies, from a read-only mount. It also finds which local user or which SMTP-authenticated domain is filling the queue, which is usually the first question when a queue explodes.

**Keeping the label count sane.** Per-sender queue metrics are limited to the top 10 (`EXIM_TOP_USERS`), and the queue scan stops at 50,000 files (`EXIM_MAX_FILES`, with `whm_exim_queue_scan_truncated` = 1 when it does).

## Security

- Every port is bound to `127.0.0.1`. Access is through an SSH tunnel.
- The exporter runs as UID 10001 in a read-only container with all capabilities dropped. It reads the Exim spool through a read-only mount and one supplementary group, not as root.
- The WHM token is only passed to the exporter. The Telegram token is written by `entrypoint.sh` to a file on the Alertmanager container's tmpfs, never to the mounted config.
- `.env` holds all secrets, is created `chmod 600` and is in `.gitignore`.
- **Shared-hosting caveat:** `127.0.0.1` keeps the internet out, but not local users. On a server where untrusted customers have shell or PHP access, they could query Prometheus (`:9090`), Alertmanager (`:9093`) or the exporter (`:9877`) and see account names and mail volumes. Grafana has a login. On such a server, add basic auth with a `--web.config.file` for Prometheus and Alertmanager (see *Ideas*).

## Tuning alerts

Thresholds live in `prometheus/rules/whm.yml` and `node.yml`. After changing them:

```bash
make test-rules    # unit tests in prometheus/tests/rules_test.yml
make validate      # promtool + amtool syntax checks
make reload        # apply without restarting
```

Silence a noisy alert for a while (for example during maintenance) in the Alertmanager UI: **New Silence**.

## Developing without a cPanel server

The test suite ships a fake WHM that serves the fixtures in `exporter/tests/fixtures/`:

```bash
cd exporter
pip install -r requirements-dev.txt
python -m pytest                                   # unit + end-to-end tests
python tests/fake_whm.py --port 8087 --token dev &
WHM_URL=http://127.0.0.1:8087 WHM_API_TOKEN=dev EXIM_SPOOL_DIR=/tmp python -m whm_exporter --once
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make check` shows `FAIL ... HTTP 403` or `Permission denied` | The token lacks a privilege for that function. Add it, or drop the collector with `COLLECTORS=` |
| `cphulk FAIL ... disabled` | cPHulk is off (common when CSF/LFD does brute-force protection). Remove `cphulk` from `COLLECTORS` |
| `exim_queue FAIL ... EXIM_SPOOL_GID` | Run `make preflight` again, then `make up` |
| Grafana panels say *No data* | Wait a minute after the first start. Then check Prometheus › Status › Targets |
| No Telegram messages | `make logs s=alertmanager`. The `AlertmanagerNotificationsFailing` alert also shows up in the UI |
| Many expired certificates in the SSL table | Usually domains that no longer point to the server, so AutoSSL can't validate them. The alert ignores already-expired certificates |

## Metrics reference

| Metric | Labels |
|---|---|
| `whm_server_info` | `version` |
| `whm_service_installed`, `_enabled`, `_monitored`, `_running` | `service` |
| `whm_accounts` | `state` (`active`, `suspended`) |
| `whm_account_info` | `user`, `domain`, `plan`, `owner` |
| `whm_account_suspended` | `user` |
| `whm_account_disk_used_bytes`, `_disk_limit_bytes`, `_inodes_used`, `_inodes_limit` | `user` |
| `whm_ssl_cert_not_after_timestamp_seconds`, `whm_ssl_cert_self_signed` | `servername`, `user` |
| `whm_ssl_cert_info` | `servername`, `user`, `issuer`, `validation_type`, `vhost_type` |
| `whm_cphulk_blocked_ips` | `list` (`brute`, `excessive`) |
| `whm_email_messages` | `user`, `domain`, `result` (`sent`, `delivered`, `failed`, `deferred`) |
| `whm_email_reached_max_per_hour`, `whm_email_reached_max_defer_fail` | `user` |
| `whm_exim_queue_messages`, `_frozen_messages`, `_bounce_messages`, `_oldest_message_age_seconds` | - |
| `whm_exim_queue_messages_by_local_user` | `user` |
| `whm_exim_queue_messages_by_auth_domain` | `domain` |
| `whm_collector_up`, `_stale`, `_duration_seconds`, `_interval_seconds`, `_last_success_timestamp_seconds`, `_runs_total`, `_errors_total` | `collector` |

## Ideas for next steps

- **MySQL:** add `mysqld_exporter` with a read-only MySQL user for connections, slow queries and InnoDB metrics.
- **Apache:** scrape `mod_status` for busy workers and requests per second.
- **Hardening for shared servers:** basic auth for Prometheus and Alertmanager via `--web.config.file` (bcrypt hashes).
- **More servers:** run only the exporter and node-exporter on other cPanel servers and scrape them from one central Prometheus over a WireGuard tunnel.
- **Log sentinel:** the log-based attack detector from the project list, feeding its own metrics into this Prometheus.

## License

MIT
