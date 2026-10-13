# Technology dependencies

Validated stable dependency resolution on this host is locked in frontend/package-lock.json and backend/requirements.txt. Install with `npm ci` and `pip install -r requirements.txt` to reproduce it. Backend requirements.in is the bounded source specification; update dependencies intentionally and rerun the phase gate when regenerating locks.

| Area | Versions validated |
|---|---|
| Runtime | Python 3.13.5, Node.js 24.14.0, npm 11.9.0, PostgreSQL 18 |
| Web | Next.js 16.4.0, React/React DOM 19.3.0, TypeScript 5.9.3 |
| UI | Tailwind CSS/PostCSS 4.3.3, TanStack Table 8.21.3, Recharts 3.10.1, Lucide React 0.577.0 |
| API | FastAPI 0.143.0, Uvicorn 0.54.0, Pydantic 2.14.1, pydantic-settings 2.15.0 |
| Database | SQLAlchemy 2.0.54, psycopg/binary 3.3.6, Alembic 1.20.0 |
| Security | pwdlib 0.3.1 + Argon2, PyJWT 2.15.1, cryptography 48.0.1 |
| Tests | pytest 9.1.1, httpx 0.28.1, Playwright 1.64.0 |

The current integration release adds React Flow (`@xyflow/react` 12, exact resolution in package-lock.json) and python-multipart 0.0.32 for secure bounded uploads. Pandas/Excel/Parquet and SQL Server/MySQL drivers are still planned and are not falsely represented as available connectors.

Docker uses node:24-alpine, python:3.13-slim, and postgres:18 maintained major lines; release deployment should pin reviewed image digests after container validation. No container build has been performed on this host.

The installed Starlette TestClient currently emits a deprecation warning about httpx. Tests pass using the supported interface today; migrate its test transport when replacing dependencies. Production API requests do not use TestClient.
