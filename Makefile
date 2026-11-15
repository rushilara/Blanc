# Blanc local dev commands. Run from the repo root.
# Demo path: cp .env.example .env → set GEMINI_API_KEY → make backend / make frontend
# → open http://localhost:3000 (local SQLite case DB; Gemini for embed/analyze).
#   make help      list common targets
#   make backend   FastAPI on http://localhost:$(BACKEND_PORT) with reload
#   make frontend  Next.js dev server on http://localhost:3000
#   make import    store extracted evidence in the local case DB, e.g.
#                  make import EVIDENCE=path/to/exports TITLE="Owl case"
#                  make import EVIDENCE=path/to/exports CASE=case_01...   (add to a case)
#   make test      backend lint + tests, frontend lint (offline: live API tests are skipped)
#   make test-live backend tests marked `live` only; calls real APIs using keys from .env
#   make coverage  backend tests with coverage (terminal report + backend/htmlcov/)

# Pick up BACKEND_PORT etc. from the root .env if present (the backend app also
# reads .env itself via pydantic-settings).
-include .env

PYTHON ?= python3.12
BACKEND_PORT ?= 8000
VENV := backend/.venv
VENV_PY := $(VENV)/bin/python
VENV_STAMP := $(VENV)/.installed

.PHONY: help backend frontend import test test-backend test-live test-frontend install-backend install-frontend coverage clean

help:
	@echo "Blanc local-first demo (case DB on this machine; Gemini for embed/analyze)"
	@echo "  1. cp .env.example .env   # set GEMINI_API_KEY; keep DATABASE_URL local"
	@echo "  2. make backend           # http://localhost:$(BACKEND_PORT)  (health: /health)"
	@echo "  3. make frontend          # http://localhost:3000  (sign-in → /workspace)"
	@echo "  4. make import EVIDENCE=path/to/exports TITLE=\"Case name\""
	@echo "     Import only the extracted evidence folder; keep answer keys and question sets elsewhere."
	@echo "Other: make test | test-live | coverage | clean"
	@echo "If the frontend origin is not http://localhost:3000, set CORS_ORIGINS in .env."

$(VENV_PY):
	$(PYTHON) -m venv $(VENV)

$(VENV_STAMP): $(VENV_PY) backend/requirements.txt backend/requirements-dev.txt
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
	touch $(VENV_STAMP)

install-backend: $(VENV_STAMP)

backend: install-backend
	cd backend && .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port $(BACKEND_PORT)

# Import extracted evidence into the local SQLite case DB (DATABASE_URL), embedding
# text chunks with Gemini. EVIDENCE is a file or folder (relative to the repo root
# or absolute)—use only the evidence export, not answer keys / question sets.
# Files are read in place, not copied. Prints the case_id on stdout.
import: install-backend
	@test -n "$(EVIDENCE)" || { echo 'usage: make import EVIDENCE=path/to/exports TITLE="Case title"  (or CASE=case_id)'; exit 2; }
	@test -n "$(TITLE)$(CASE)" || { echo 'make import: set TITLE="..." for a new case, or CASE=case_id to add to one'; exit 2; }
	cd backend && .venv/bin/python -m app.evidence "$(abspath $(EVIDENCE))" --store \
		$(if $(CASE),--case-id "$(CASE)",--title "$(TITLE)") $(IMPORT_ARGS)

install-frontend:
	cd frontend && npm install

frontend: install-frontend
	cd frontend && npm run dev

test-backend: install-backend
	cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q

# Real-API tests (pytest marker `live`; deselected by default and in CI). The backend
# settings read GEMINI_API_KEY from the root .env; tests skip if it is not set.
test-live: install-backend
	cd backend && .venv/bin/pytest -q -m live -rs

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
