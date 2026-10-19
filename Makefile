# Common commands. Run `make help` for the list.
BACKEND_PY := backend/.venv/bin
PY ?= python3

.PHONY: desktop-setup desktop-run desktop-test desktop-e2e ext-test ext-e2e ext-e2e-chrome ext-demo learning-eval audit backup help setup up down db-init dev-api dev-web lint typecheck test build format check smoke e2e clean-test-data benchmark worker scanner-up scanner-down scanner-check

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies, create .env if missing
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example: set a real KMR_POSTGRES_PASSWORD (and the same value in KMR_DATABASE_URL)")
	$(PY) -m venv backend/.venv
	$(BACKEND_PY)/pip install -q -e "backend[dev]"
	cd frontend && pnpm install

up: ## Start local Postgres and Redis
	docker compose up -d

down: ## Stop local Postgres and Redis (data volume is kept)
	docker compose down

db-init: ## Create the restricted app DB role and apply migrations (needs `make up`)
	cd backend && .venv/bin/python -m app.db.bootstrap && .venv/bin/alembic upgrade head

dev-api: ## Run the API on http://127.0.0.1:8000
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-web: ## Run the web app on http://localhost:3000
	cd frontend && pnpm dev

lint: ## Lint backend and frontend
	cd backend && .venv/bin/ruff check .
	cd frontend && pnpm lint

typecheck: ## Type-check backend and frontend
	cd backend && .venv/bin/mypy app tests
	cd frontend && pnpm typecheck

test: ## Run backend and frontend unit tests
	cd backend && .venv/bin/pytest
	cd frontend && pnpm test

build: ## Production build of the frontend
	cd frontend && pnpm build

format: ## Format backend and frontend
	cd backend && .venv/bin/ruff format .
	cd frontend && pnpm format

check: ## Everything that must pass before a phase is accepted (needs `make up` + `make db-init`; no running servers)
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd backend && .venv/bin/mypy app tests
	cd backend && .venv/bin/pytest
	cd frontend && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build

benchmark: ## Measure the diagnosis rules against the labeled scenarios (see benchmarks/diagnosis)
	cd backend && .venv/bin/python -m app.diagnosis.benchmark --set both --provider rules --fail-on-regression

worker: ## Run the scan worker (needs `make up`, `make scanner-up`)
	cd backend && .venv/bin/python -m app.worker

scanner-up: ## Build and start the isolated scanner sandbox (browser + egress proxy containers)
	docker compose --profile scanner up -d --build scan-proxy scan-browser

scanner-down: ## Stop the scanner sandbox
	docker compose --profile scanner stop scan-browser scan-proxy

scanner-check: ## Verify the real sandbox: isolation, hardening, SSRF blocks, a public-site scan
	cd backend && .venv/bin/python -m app.scanning.system_check

clean-test-data: ## Delete users/orgs created by smoke and e2e runs (local only; add DRY=1 to preview)
	cd backend && .venv/bin/python -m app.db.clean_test_data $(if $(DRY),--dry-run,)

e2e: ## Phone-size browser flow: register, org, project, members, sign out. Creates dev DB rows
	cd frontend && BASE_URL=http://localhost:3000 node scripts/e2e-flow.mjs

audit: ## Known-vulnerability check of Python and JavaScript dependencies (needs network)
	cd backend && .venv/bin/pip-audit --skip-editable
	cd frontend && pnpm audit --prod

backup: ## Back up the dev database and evidence files into ./backups
	bash scripts/backup.sh

smoke: ## Responsive smoke check (start dev-web or `pnpm start` first; CHROME_PATH sets the browser)
	cd frontend && node scripts/responsive-smoke.mjs

learning-eval: ## Run the learning-support benchmark (author-written cases, not teacher-reviewed)
	cd backend && .venv/bin/python -m app.assistant.evaluate

ext-test: ## Test the Chrome extension (uses the frontend's vitest; run `make setup` first)
	cd extension && ../frontend/node_modules/.bin/vitest run

ext-e2e: ## Real-Chromium test of the extension against the local API (needs `make up`, db-init and `make dev-api`)
	cd backend && PYTHONPATH=. .venv/bin/python ../extension/e2e/run_e2e.py

ext-e2e-chrome: ## Same as ext-e2e, but in your installed Google Chrome (headless; loaded like "Load unpacked")
	cd backend && PYTHONPATH=. .venv/bin/python ../extension/e2e/run_e2e.py --chrome

ext-demo: ## Set up a throwaway account, project, token and test page to try the extension by hand in Chrome
	cd backend && PYTHONPATH=. .venv/bin/python ../extension/e2e/demo.py

desktop-setup: ## Create the agent's Python venv (uses system PyGObject for the accessibility bus) and install the app's packages
	cd desktop/agent && /usr/bin/python3 -m venv --system-site-packages .venv && .venv/bin/pip install -q httpx pytest ruff mypy
	cd desktop/app && pnpm install

desktop-run: ## Start the desktop assistant window (Linux dev run)
	cd desktop/app && pnpm start

desktop-test: ## Test the desktop agent (pytest) and the chat window's logic (vitest)
	cd desktop/agent && .venv/bin/ruff check . && .venv/bin/mypy kmr_desktop && .venv/bin/pytest -q
	cd desktop/app && node_modules/.bin/vitest run

desktop-e2e: ## Electron + real agent + real GTK window + the running API (needs `make dev-api`)
	cd desktop/app && node test/e2e.mjs
