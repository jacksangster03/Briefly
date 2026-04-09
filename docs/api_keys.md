# API Keys

## Required for the briefing MVP

- `FINNHUB_API_KEY`
- `FRED_API_KEY`
- `SEC_USER_AGENT`

## Optional but useful

- `NEWSAPI_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `EMAIL_*`

## Not needed for the current MVP

- `POLYGON_API_KEY`
- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `X_BEARER_TOKEN`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

## Safety

- Keep secrets in `.env`
- `.env` is ignored by git
- Never place real keys in `.env.example`, README files, screenshots, or committed source files
