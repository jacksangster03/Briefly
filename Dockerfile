FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create data directories
RUN mkdir -p data/state data/raw data/processed data/cache logs

# Initialise database
RUN python -c "from app.db.session import init_db; init_db()"

# Default: run the scheduler
CMD ["python", "-m", "app.cli", "scheduler"]
