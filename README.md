# Reconviora — Data Reconciliation & Visualization Platform

Working application built with Next.js, React, TypeScript, Tailwind, FastAPI, SQLAlchemy, Alembic, and PostgreSQL. The external-integration release adds an accessible Navy Blue + Teal design, real REST/PostgreSQL connectors, OAuth PKCE, authorized previews, bounded ingestion, table creation, mapping, reconciliation, and detailed results. It is not yet certified for production deployment.

Implemented: secure sign-in/sign-out, role checks, saved preferences, live dashboard, integration catalog/setup wizard, encrypted credentials, OAuth code flow with PKCE/state validation/server-side exchange/refresh/scopes, API key/bearer/HTTPS Basic auth for generic REST, PostgreSQL TLS connections, ServiceNow Table API adapter, owned resource previews/schema approval, full/incremental PostgreSQL staging, encrypted validated CSV uploads, dynamic table DDL preview/creation/registration, Medallion assignments, React Flow/table mapping forms, per-target reconciliation, aggregation/joins/tolerances/null/duplicate checks, unique-source fan-out coverage, differences and CSV export. No simulated production data or authentication.

**Start with [Integration setup and supported capabilities](docs/integrations.md).** Existing accounts and metadata are preserved; there is no need to recreate your administrator. Apply `alembic upgrade head` before restarting the updated backend.

[Current release verification](docs/integration-verification.md): 47 backend tests and 12 desktop/mobile browser checks. Original foundation documentation is retained as a labeled historical design snapshot.

[System and frontend/backend architecture](docs/architecture.md) · [PostgreSQL schema](docs/metadata-schema.md) · [API contract and phase boundaries](docs/api.md) · [Dependencies](docs/dependencies.md) · [Verification report](docs/verification.md)

## Requirements

- Python 3.13, Node.js 24 LTS/npm, PostgreSQL 18 (or Docker Desktop with Compose).
- An available loopback port 55432 for metadata, 8000 for API, and 3000 for frontend.
- Package registry access for dependency installation.
- Modern Chrome/Edge or Safari. Physical Windows/macOS/iOS compatibility must be validated before release; see verification report.

Run commands from this directory unless indicated. Secrets are generated locally; there is no default administrator password. `.env`, `.runtime`, virtual environments, and dependency/build folders are ignored by Git. Keep local environment/password files private. The project database scripts use a separate project-owned cluster, not an existing system service.

## Windows PowerShell — native setup

Install Python/Node/PostgreSQL first. Use `npm.cmd` to avoid npm.ps1 execution-policy issues. If local PowerShell scripts are blocked, use `powershell -ExecutionPolicy Bypass -File .\scripts\dev-db.ps1 start` for this invocation without changing machine policy.

```powershell
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\backend\.venv\Scripts\python.exe scripts/init_env.py
.\scripts\dev-db.ps1 start
.\backend\.venv\Scripts\alembic.exe -c backend/alembic.ini upgrade head
Push-Location backend
.\.venv\Scripts\python.exe -m app.bootstrap
Pop-Location
Push-Location frontend
npm.cmd ci
npm.cmd run build
Pop-Location
```

`init_env.py` refuses to overwrite existing secrets. `dev-db.ps1` defaults to `C:\Program Files\PostgreSQL\18\bin`; pass `-PgBin 'your installed bin path'` if needed. Enter your own administrator email, display name, organization name, and password at the bootstrap prompts. Account creation is available from Settings after sign-in.

In separate terminals:

```powershell
# Terminal 1: API
Set-Location backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Terminal 2: frontend
Set-Location frontend
npm.cmd start
```

Open **http://localhost:3000** and sign in. API documentation: http://localhost:8000/api/docs. A browser `GET /api/health/ready` is proxied from Next.js to FastAPI and performs a PostgreSQL query. For development editing, use `npm.cmd run dev` instead of the production frontend after stopping the current frontend process.

## macOS Terminal — native setup

Install Python 3.13, Node 24, and PostgreSQL 18 using your preferred package manager. With Homebrew, the database helper discovers `brew --prefix postgresql@18`; otherwise set `PG_BIN` to your PostgreSQL binaries. The isolated cluster must be initialized by the ordinary user, not root.

```bash
python3.13 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
backend/.venv/bin/python scripts/init_env.py
bash scripts/dev-db.sh start
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
(cd backend && .venv/bin/python -m app.bootstrap)
(cd frontend && npm ci && npm run build)
```

In separate terminals:

```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm start
```

Open http://localhost:3000. Stop API/frontend with Ctrl+C. Stop the project database with `bash scripts/dev-db.sh stop` or `.\scripts\dev-db.ps1 stop` on Windows. No recursive data deletion is needed.

## Docker Compose

Generate `.env` first using the native setup's Python environment. Do not run the native cluster and Compose database concurrently on port 55432.

```text
docker compose up --build -d
docker compose exec backend python -m app.bootstrap
```

Compose waits for PostgreSQL, applies migrations in a separate service, starts the API, then starts the frontend after API readiness. Use `docker compose logs` for status. `docker compose down` stops services without deleting the metadata volume. Frontend uses a standalone Next.js server; all containers run on the server, with iOS accessing the frontend through a browser/PWA.

Compose is a **local development deployment**. Before production, configure HTTPS ingress, `APP_ENV=production`, `COOKIE_SECURE=true`, HTTPS `ALLOWED_ORIGINS`, secret injection, separate migration/runtime database roles, distributed login rate limiting, backup/restore procedures, and security review. The generated local cluster role is for development only. Container builds and deployment are supplied but not verified on this host because Docker is not installed.

## Tests

Backend tests run migrations in a disposable randomly named PostgreSQL schema and rollback per-test mutations. They require the development database user to have CREATE SCHEMA permission. Never use production credentials for tests.

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\alembic.exe check
.\.venv\Scripts\python.exe -m pip check
Pop-Location
Push-Location frontend
npm.cmd run typecheck
npx.cmd playwright install chromium webkit
Pop-Location
# API and production frontend must be running.
.\scripts\browser-tests.ps1
```

The Windows browser-test helper creates a random temporary engineer, loads credentials only into process environment, runs desktop Chromium and mobile/tablet WebKit tests, then removes that account. It does not use or change your administrator account. For macOS:

```bash
(cd backend && .venv/bin/python -m pytest tests -q && .venv/bin/alembic check)
(cd frontend && npm run typecheck && npx playwright install chromium webkit)
backend/.venv/bin/python scripts/browser_tests.py
```

Use `scripts/e2e_account.py remove` to clean up if a test process is interrupted. The browser trace-on-failure artifacts may contain session information; keep ignored test artifacts private. Screenshots are stored under `.runtime/screenshots` for local QA.

## PWA and device access

The production build registers a service worker and install manifest. Chrome/Edge provide installation on supported devices; iPhone/iPad use Safari → Share → Add to Home Screen. Install functionality requires HTTPS or localhost. For real phones/tablets, deploy behind HTTPS with a reachable hostname and set that origin in ALLOWED_ORIGINS. `localhost` on a phone refers to the phone, not this development computer.

Only the offline notice is cached. Business data, API responses, authenticated HTML, and credentials are never intentionally stored by the service worker. Offline users must reconnect to use the workspace.

## Repository layout

```text
frontend/   app, components, features, hooks, services, types, public, tests
backend/    app/api, core, models, schemas, services, repositories
            app/connectors, ingestion, reconciliation (documented future boundaries)
            migrations, tests
database/   sample_data (transaction fixtures for future phase tests)
docs/       architecture, metadata-schema, api, verification
scripts/    environment/database/test setup and code-native icon generation
```

## Scope and known limits

REST, PostgreSQL, CSV, and ServiceNow workflows are executable. Other provider-specific cards are explicitly planned, not connected. SQL Server/MySQL, Excel/Parquet import, large durable background jobs, provider-specific webhook protocols, advanced transforms, dataset-wide filtered exports/Excel export, and application OIDC/SAML SSO are not enabled. The connector architecture provides extension boundaries; these are not falsely advertised as working features.

The latest requirements supersede the original two-color palette. The interface now uses Navy #14213D, Teal #00B8A9, and neutral white/slate/light gray. Teal buttons use navy text for contrast. Keyboard focus, labels, mobile navigation, and reduced-motion support are retained. Actual macOS/iOS devices, Edge/Safari release builds, Docker, accessibility audit, load testing, and provider tenant authorization still require release verification. See docs/integrations.md for execution bounds and operational requirements.
