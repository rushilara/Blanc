# Blanc local dev commands. Run from the repo root.
#   make backend   FastAPI on http://localhost:$(BACKEND_PORT) with reload
#   make frontend  Next.js dev server on http://localhost:3000
#   make test      backend lint + tests, frontend lint
#   make coverage  backend tests with coverage (terminal report + backend/htmlcov/)

# Pick up BACKEND_PORT etc. from the root .env if present (the backend app also
# reads .env itself via pydantic-settings).
-include .env

PYTHON ?= python3.12
BACKEND_PORT ?= 8000
VENV := backend/.venv
VENV_PY := $(VENV)/bin/python
VENV_STAMP := $(VENV)/.installed

.PHONY: backend frontend test test-backend test-frontend install-backend install-frontend coverage clean

$(VENV_PY):
	$(PYTHON) -m venv $(VENV)

$(VENV_STAMP): $(VENV_PY) backend/requirements.txt backend/requirements-dev.txt
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
	touch $(VENV_STAMP)

install-backend: $(VENV_STAMP)

backend: install-backend
	cd backend && .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port $(BACKEND_PORT)

install-frontend:
	cd frontend && npm install

frontend: install-frontend
	cd frontend && npm run dev

test-backend: install-backend
	cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q

test-frontend: install-frontend
	cd frontend && npm run lint

test: test-backend test-frontend

# Same coverage run as CI; fails below fail_under in backend/pyproject.toml.
# pytest-cov is pinned in backend/requirements-dev.txt.
coverage: install-backend
	cd backend && .venv/bin/pytest -q --cov --cov-report=term-missing --cov-report=xml --cov-report=html
	@echo "HTML report: backend/htmlcov/index.html"

clean:
	rm -rf $(VENV) frontend/node_modules frontend/.next
