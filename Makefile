.PHONY: install run test lint demo
install:
	python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
run:
	uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
test:
	pytest -q
lint:
	ruff check .
demo:
	python scripts/create_demo_data.py
