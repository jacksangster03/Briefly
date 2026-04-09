# Scheduling

The project supports three ways to run:

## Manual

```bash
python -m app.cli morning
python -m app.cli intraday
python -m app.cli breaking
```

## APScheduler

```bash
python -m app.cli scheduler
```

Configured defaults in `configs/schedules.yaml`:

- Morning briefing: `12:30` Europe/Madrid
- Intraday updates: hourly from `14:30` to `22:00`
- Breaking checks: every `5` minutes

## Docker

```bash
docker compose up -d
```

Breaking alerts poll on an interval and only send when an event clears the configured threshold.
