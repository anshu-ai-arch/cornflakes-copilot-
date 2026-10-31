.PHONY: test eval run chaos build up down

test:
	./venv/bin/pytest tests/ -v

eval:
	./venv/bin/python -m eval.evaluator

run:
	./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

chaos:
	bash scripts/chaos.sh

build:
	docker compose build

up:
	docker compose up --build

down:
	docker compose down -v
