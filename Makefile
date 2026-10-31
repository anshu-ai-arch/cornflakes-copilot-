.PHONY: test eval run chaos build up down

PYTHON ?= $(shell which python3 2>/dev/null || which python 2>/dev/null || echo ./venv/bin/python)
PYTEST ?= $(shell which pytest 2>/dev/null || echo $(PYTHON) -m pytest)

test:
	@if [ -f "./venv/bin/pytest" ]; then ./venv/bin/pytest tests/ -v; else $(PYTEST) tests/ -v; fi

eval:
	@if [ -f "./venv/bin/python" ]; then ./venv/bin/python -m eval.evaluator; else $(PYTHON) -m eval.evaluator; fi

run:
	@if [ -f "./venv/bin/uvicorn" ]; then ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload; else $(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload; fi

chaos:
	@if [ -f "./venv/bin/python" ]; then ./venv/bin/python scripts/chaos.py; else $(PYTHON) scripts/chaos.py; fi

build:
	docker compose build

up:
	docker compose up --build

down:
	docker compose down -v
