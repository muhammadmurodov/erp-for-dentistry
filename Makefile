# Developer entry points. `make help` lists them.

SHELL := /bin/bash
BACKEND := backend
FRONTEND := frontend
PY := $(BACKEND)/.venv/bin

.DEFAULT_GOAL := help
.PHONY: help setup setup-backend setup-frontend migrate migration seed \
        dev-backend dev-frontend test test-backend test-frontend \
        lint fmt typecheck check reset-db up down logs clean

help: ## List the available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: setup-backend setup-frontend ## Install all dependencies

setup-backend: ## Create the backend venv and install dependencies
	cd $(BACKEND) && uv venv && uv pip install -e ".[dev]"

setup-frontend: ## Install frontend dependencies
	cd $(FRONTEND) && npm install

migrate: ## Apply database migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

migration: ## Autogenerate a migration (make migration m="what changed")
	@test -n "$(m)" || (echo 'Pass a message: make migration m="add journal entry"'; exit 1)
	cd $(BACKEND) && .venv/bin/alembic revision --autogenerate -m "$(m)"

seed: ## Create the fictional demo organization
	cd $(BACKEND) && .venv/bin/python -m app.cli seed-demo

dev-backend: ## Run the API with reload
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-frontend: ## Run the Vite dev server
	cd $(FRONTEND) && npm run dev

test: test-backend test-frontend ## Run every test suite

test-backend: ## Run the backend tests
	cd $(BACKEND) && .venv/bin/python -m pytest tests -v

test-frontend: ## Type-check and build the frontend
	cd $(FRONTEND) && npm run typecheck && npm run build

lint: ## Lint both sides
	cd $(BACKEND) && .venv/bin/ruff check app tests alembic
	cd $(FRONTEND) && npm run lint

fmt: ## Format the backend and fix what can be fixed
	cd $(BACKEND) && .venv/bin/ruff check --fix app tests alembic && .venv/bin/ruff format app tests alembic

typecheck: ## Type-check both sides
	cd $(BACKEND) && .venv/bin/mypy app
	cd $(FRONTEND) && npm run typecheck

check: lint test ## What CI runs

reset-db: ## Drop, recreate, migrate and reseed the dev database (destructive)
	@echo "This deletes the development database. Ctrl-C within 3s to stop."
	@sleep 3
	cd $(BACKEND) && .venv/bin/python -c "\
import subprocess, sys; \
from app.core.config import get_settings; \
s = get_settings(); \
sys.exit(0 if not s.is_production else (print('Refusing: APP_ENV is production'), 1)[1])"
	psql -U $${POSTGRES_USER:-$$USER} -d postgres -c "DROP DATABASE IF EXISTS $${POSTGRES_DB:-erp_dev}"
	psql -U $${POSTGRES_USER:-$$USER} -d postgres -c "CREATE DATABASE $${POSTGRES_DB:-erp_dev}"
	$(MAKE) migrate seed

up: ## Start the Docker Compose stack
	docker compose up -d --build

down: ## Stop the Docker Compose stack
	docker compose down

logs: ## Follow the stack logs
	docker compose logs -f --tail=100

clean: ## Remove build artefacts and caches
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache $(BACKEND)/.mypy_cache \
	       $(BACKEND)/htmlcov $(BACKEND)/.coverage $(FRONTEND)/dist
	find $(BACKEND) -name '__pycache__' -type d -prune -exec rm -rf {} +
