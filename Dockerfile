FROM python:3.12-slim

WORKDIR /app

# Install dependencies before copying source so this layer is cached on code-only changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create persistent data directories (overridden by volume mounts at runtime)
RUN mkdir -p data/state data/raw data/processed data/cache logs backups

# Initialise database schema
RUN python -c "from app.db.session import init_db; init_db()"

# Lightweight liveness check: verify the CLI entry-point imports cleanly
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import app.cli" || exit 1

# Default: run the scheduler (override with docker compose run for one-shot commands)
CMD ["python", "-m", "app.cli", "scheduler"]
