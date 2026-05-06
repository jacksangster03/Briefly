# Always-on Deployment Guide

Briefly is designed to run continuously on an always-on machine so that the scheduler fires
at the correct local time every market day without manual intervention. This guide covers
Docker (recommended for servers), Linux systemd, and macOS launchd, plus data persistence,
backup, and environment configuration.

---

## Critical caveat: local Mac, sleep, and power-off

If you run Briefly on a MacBook or iMac that is ever closed, put to sleep, or fully powered off,
**the scheduler will not run while the machine is off.** No brief will be sent during that window.
The startup catch-up (Phase 9.1) will deliver any sessions that elapsed today _after_ the
machine wakes and the scheduler starts, but sessions from yesterday or earlier are not
automatically recovered.

If reliable daily delivery matters, use one of the following:

- A cheap Linux VPS (€3–6/month: Hetzner CX11, DigitalOcean Droplet, Linode Nanode)
- A Raspberry Pi 4/5 on your home network running 24/7
- An always-on Mac Mini or Mac Studio with sleep disabled (`sudo pmset -a sleep 0`)

---

## Option A: Docker (recommended for VPS or home server)

Docker is the simplest path to a reproducible, isolated, always-on deployment. It requires
no Python environment management on the host and restarts automatically after reboots.

### Prerequisites

- Docker Engine 24+ and Docker Compose v2+
- A `.env` file in the project root (copy `.env.example` and fill in your values)

### Quick start

```bash
git clone https://github.com/jacksangster03/Briefly.git
cd Briefly
cp .env.example .env        # fill in API keys and delivery credentials
make docker-up              # builds image and starts scheduler in background
docker compose logs -f      # follow logs
```

The `briefly` service starts the scheduler. It restarts automatically on crash or reboot
(`restart: unless-stopped`). Shut it down cleanly with `make docker-down`.

### One-shot manual commands

```bash
# Send the current session manually (does not affect idempotency)
docker compose run --rm briefly python -m app.cli brief

# Send the morning brief explicitly
docker compose run --rm briefly python -m app.cli morning

# Run the web control center alongside the scheduler
docker compose up -d briefly
docker compose run --rm -p 8080:8080 briefly python -m app.cli web
```

### Updating

```bash
git pull
docker compose down
docker compose up -d --build
```

### Data volume layout

The compose file mounts three host directories into the container:

| Host path | Container path | Contents |
|-----------|---------------|----------|
| `./data/` | `/app/data/` | SQLite database (`data/state/market_briefing.db`), price cache, processed data |
| `./logs/` | `/app/logs/` | Rotating application logs |
| `./configs/` | `/app/configs/` | Profile YAML, watchlists, healthcare config |

These directories are created automatically by `make setup`. **Do not delete them** between
container rebuilds: the database is your live state.

---

## Option B: Linux systemd (Ubuntu / Debian VPS)

Systemd is the native service manager on most Linux distributions. It handles restart policy,
log capture via journald, and start-on-boot automatically.

### Prerequisites

- Python 3.11+ and a virtual environment at `/opt/briefly/.venv`
- A `.env` file at `/opt/briefly/.env`

### Install

```bash
# Clone and set up
sudo mkdir -p /opt/briefly
sudo chown $USER:$USER /opt/briefly
git clone https://github.com/jacksangster03/Briefly.git /opt/briefly
cd /opt/briefly
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
cp .env.example .env        # fill in values

# Run database init
.venv/bin/python -m app.cli init-db

# Install the service units
sudo cp scripts/briefly.service /etc/systemd/system/briefly-scheduler.service
# Edit the unit to set User=, WorkingDirectory=, and EnvironmentFile= correctly
sudo systemctl daemon-reload
sudo systemctl enable briefly-scheduler
sudo systemctl start briefly-scheduler
sudo systemctl status briefly-scheduler
```

### Unit file

A template is provided at `scripts/briefly.service`. Edit the `User`, `WorkingDirectory`,
and `EnvironmentFile` fields to match your install path:

```ini
[Unit]
Description=Briefly scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=briefly
WorkingDirectory=/opt/briefly
EnvironmentFile=/opt/briefly/.env
ExecStart=/opt/briefly/.venv/bin/python -m app.cli scheduler
Restart=on-failure
RestartSec=15
StartLimitIntervalSec=120
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
```

### Useful commands

```bash
sudo systemctl start   briefly-scheduler
sudo systemctl stop    briefly-scheduler
sudo systemctl restart briefly-scheduler
sudo systemctl status  briefly-scheduler
journalctl -u briefly-scheduler -f          # follow live logs
journalctl -u briefly-scheduler --since today
```

### Running the web panel as a second unit

Copy `scripts/briefly.service`, rename it `briefly-web.service`, and change `ExecStart`:

```ini
ExecStart=/opt/briefly/.venv/bin/python -m app.cli web --host 0.0.0.0 --port 8080
```

Expose port 8080 only behind a reverse proxy (nginx/caddy) — never bind `0.0.0.0` directly
to the internet without authentication.

---

## Option C: macOS launchd (always-on Mac)

If you are running Briefly on a Mac Mini or Mac Studio that stays on permanently, launchd
is the native way to keep the scheduler and web panel alive.

### Prerequisites

- A virtual environment at `<project>/.venv`
- A `.env` file in the project root

### Install

```bash
cd /path/to/Briefly
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
cp .env.example .env        # fill in values

# Install and start both services
./scripts/service.sh install
./scripts/service.sh start
./scripts/service.sh status
```

### Control

```bash
./scripts/service.sh restart
./scripts/service.sh stop
./scripts/service.sh logs            # tail all logs
./scripts/service.sh logs-scheduler  # scheduler only
./scripts/service.sh logs-web        # web panel only
```

### Preventing sleep on macOS

```bash
# Disable all sleep modes permanently (display can still dim)
sudo pmset -a sleep 0 disksleep 0

# Or: System Settings > Battery > Prevent Mac from sleeping automatically when display is off
```

With sleep disabled and `KeepAlive=true` in the launchd plist, the scheduler restarts
automatically if it crashes, and starts again after a reboot.

**Reminder:** A MacBook with the lid closed and not plugged in will still sleep or power off
eventually regardless of pmset. Use a desktop Mac or a VPS for guaranteed 24/7 uptime.

---

## Environment variable checklist

Copy `.env.example` to `.env` and work through the tiers below.

### Required

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Delivery channel (get from @BotFather) |
| `TELEGRAM_CHAT_ID` | Your personal or group chat ID |
| `FINNHUB_API_KEY` | Market quotes and news (free tier: 60 calls/min) |
| `FRED_API_KEY` | Macro data: rates, CPI, unemployment (free, unlimited) |
| `SEC_USER_AGENT` | SEC EDGAR fair-access string, e.g. `Briefly your@email.com` |
| `TIMEZONE` | Your local timezone, e.g. `Europe/Madrid` |
| `DRY_RUN` | Set `false` in production; keeps `true` during initial testing |

### Recommended

| Variable | Purpose |
|----------|---------|
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_USER` / `EMAIL_PASSWORD` / `EMAIL_TO` | HTML email delivery with inline charts |
| `DATABASE_URL` | Defaults to `sqlite:///data/state/market_briefing.db`; keep the default unless you move the data directory |
| `OPENAI_API_KEY` | Required only if `ENABLE_LLM_EMAIL_RENDER=true` |
| `LLM_RENDER_SHADOW_MODE` | Keep `true` for the first week to validate LLM output without live delivery |
| `LLM_EMAIL_INPUT_COST_PER_1M_TOKENS` / `LLM_EMAIL_OUTPUT_COST_PER_1M_TOKENS` | Enable cost tracking and budget enforcement |
| `LLM_MONTHLY_BUDGET_USD` | Hard cap on monthly LLM spend; `0` disables the cap |
| `LOG_LEVEL` | `INFO` in production; `DEBUG` for troubleshooting |

### Optional (additional news providers)

| Variable | Purpose |
|----------|---------|
| `NEWSAPI_KEY` | Extra news headlines |
| `ALPHA_VANTAGE_API_KEY` | Sentiment-tagged finance news |
| `FMP_API_KEY` | Financial Modeling Prep news |
| `MARKETAUX_API_KEY` | Finance-focused feed |
| `MEDIASTACK_API_KEY` | Low-volume backup feed |
| `BLS_API_KEY` | Official US labour statistics |
| `BEA_API_KEY` | Official US GDP and income data |

Set `ENABLE_GDELT=true`, `ENABLE_ALPHA_VANTAGE_NEWS=true`, etc. to activate each provider.

---

## Persistent data and SQLite

### What is stored

| Path | Contents |
|------|----------|
| `data/state/market_briefing.db` | All application state: events, sessions, delivery records, portfolio data, LLM usage logs |
| `data/cache/` | Provider response cache (safe to delete; rebuilt on next run) |
| `data/raw/` | Unprocessed provider payloads (safe to delete) |
| `data/processed/` | Normalised event store (rebuilt from raw) |
| `logs/` | Rotating application and service logs |
| `configs/` | User profile YAML, watchlists, healthcare config |

`data/state/market_briefing.db` is the only file that cannot be recreated automatically. All
other directories are either caches or logs.

### Backup strategy

A simple daily backup of the database and configs is sufficient:

```bash
# Minimal backup (cron or systemd timer)
DATE=$(date +%Y-%m-%d)
cp data/state/market_briefing.db backups/market_briefing_${DATE}.db
cp -r configs/ backups/configs_${DATE}/

# Or rsync to a remote host
rsync -a data/state/market_briefing.db user@backup-host:/backups/briefly/
```

SQLite supports hot backups via the `.backup` command without stopping the process:

```bash
sqlite3 data/state/market_briefing.db ".backup backups/market_briefing_$(date +%Y-%m-%d).db"
```

Prune old snapshots periodically to keep the database compact:

```bash
python -m app.cli snapshots prune --retention-days 30
```

Suggested cron (runs at 02:00 daily):

```
0 2 * * * cd /opt/briefly && sqlite3 data/state/market_briefing.db ".backup backups/market_briefing_$(date +\%Y-\%m-\%d).db" && find backups/ -name "*.db" -mtime +30 -delete
```

### Restore

```bash
# Stop the scheduler before restoring
systemctl stop briefly-scheduler   # or ./scripts/service.sh stop

# Replace the database
cp backups/market_briefing_2026-05-01.db data/state/market_briefing.db

# Restart
systemctl start briefly-scheduler
```

---

## Restart policy summary

| Method | Restart on crash | Restart on reboot |
|--------|-----------------|------------------|
| Docker (`restart: unless-stopped`) | Yes | Yes (after Docker daemon starts) |
| systemd (`Restart=on-failure`) | Yes | Yes (enabled unit) |
| launchd (`KeepAlive=true`) | Yes | Yes (per-user session) |

All three methods restart the scheduler within seconds of a crash. None will recover a missed
session from a previous day automatically; the Phase 9.1 startup catch-up handles sessions
that elapsed since midnight _today_ only.

---

## Monitoring

Check that briefs are being delivered by watching:

```bash
# Recent delivery log
python -m app.cli snapshots list --date today

# LLM usage and cost this month
python -m app.cli llm-usage summary

# Process health
./scripts/service.sh status          # launchd
systemctl status briefly-scheduler   # systemd
docker compose ps                    # Docker
```

Application logs rotate automatically and are written to `logs/`. For Docker, use
`docker compose logs -f briefly`. For systemd, use `journalctl -u briefly-scheduler -f`.
