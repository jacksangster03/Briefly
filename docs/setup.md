# Setup

## Local setup

```bash
cd /Users/jack/briefly
pip install -e ".[all]"
make setup
```

If the repository has not been renamed yet, use `/Users/jack/market-briefing-bot`.

Create and fill:

- `.env`
- `configs/user_profile.yaml`
- `configs/watchlists.yaml`

## First smoke test

```bash
make test
python -m app.cli --dry-run status
python -m app.cli --dry-run morning
python -m app.cli --dry-run intraday
python -m app.cli --dry-run breaking
```

## Real Telegram delivery

Set in `.env`:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DRY_RUN=false
```

Then run:

```bash
python -m app.cli morning
```
