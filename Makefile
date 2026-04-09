.PHONY: install dev setup run-morning run-intraday run-alerts test lint clean docker-up docker-down

install:
	pip install -e .

dev:
	pip install -e ".[all]"

setup:
	cp -n .env.example .env || true
	cp -n configs/user_profile.example.yaml configs/user_profile.yaml || true
	cp -n configs/watchlists.example.yaml configs/watchlists.yaml || true
	mkdir -p data/state data/raw data/processed data/cache logs
	python -c "from app.db.session import init_db; init_db()"
	@echo "Setup complete. Edit .env and configs/ before running."

run-morning:
	python scripts/run_morning_brief.py

run-intraday:
	python scripts/run_intraday_monitor.py

run-alerts:
	python scripts/run_breaking_alerts.py

run-scheduler:
	python -m app.main

test:
	pytest app/tests/ -v --tb=short

test-cov:
	pytest app/tests/ -v --cov=app --cov-report=term-missing

lint:
	ruff check app/
	ruff format --check app/

format:
	ruff format app/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache htmlcov .coverage dist build *.egg-info

docker-up:
	docker compose up -d

docker-down:
	docker compose down

dry-run:
	DRY_RUN=true python scripts/run_morning_brief.py
