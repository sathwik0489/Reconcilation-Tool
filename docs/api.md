# REST API contract

> The foundation endpoints below remain supported. The original schedule at the end is historical; see [current integration and workflow endpoints](integrations.md#api-additions) or development OpenAPI for the implemented release.

All API paths start `/api`. The browser uses Next.js same-origin rewrites; the backend is private in production. Session cookie is mandatory except login/liveness/readiness. Mutations require an `Origin` listed in ALLOWED_ORIGINS, including command-line clients. Standard validation errors use HTTP 422; auth 401; role/origin 403; missing resources 404; duplicates 409; rate limiting 429; database unavailability 503. SQL/driver details are never returned.

## Implemented in Phase 1

| Method | Path | Behavior |
|---|---|---|
| GET | /health/live | Process liveness |
| GET | /health/ready | Real SELECT 1, migration revision, seeded layers |
| POST | /auth/login | Validated email/password → secure session cookie and safe user fields |
| GET | /auth/me | Current account |
| POST | /auth/logout | Revoke account sessions, delete cookie |
| PATCH | /settings/preferences | Own display_name, page_size (10/25/50), compact_tables |
| GET/POST | /settings/users | Admin-only paginated accounts / create account with role |
| GET | /system/status | Live readiness plus module phase/count inventory |
| GET | /dashboard/summary | Live metadata/result aggregates, layers, latest audit entries |
| GET | /connections | Safe connection metadata; never password/encrypted credential |
| GET | /tables | Registered table metadata |
| GET | /tables/{id} | UUID-validated metadata detail |
| GET | /tables/{id}/schema | Registered column metadata |
| GET | /ingestion/runs | Persisted execution metadata |
| GET | /mappings | Persisted mapping metadata |
| GET | /reconciliation/runs | Persisted execution metadata |

Inventory queries accept `page>=1`, `page_size=1..100`, and `search<=100 characters`. Search matches literal substrings with escaped SQL wildcard characters. Responses: `{items, total, page, page_size}`. GET schema returns `{items}`. Routes return real empty lists on a fresh database; they are not seeded with fictional jobs or tables. `/api/docs` and `/api/openapi.json` are enabled in development only.

## Scheduled endpoints, not yet implemented

| Phase | Endpoints |
|---|---|
| 2 | POST /connections; POST /connections/test; PATCH/DELETE /connections/{id}; GET /connections/{id}/schemas; GET /connections/{id}/tables; GET /connections/{id}/tables/{name}/schema and /preview |
| 2 | POST /tables/ddl-preview; POST /tables (create or register); PATCH /tables/{id}; GET /tables/{id}/preview; POST /ingestion/preview; POST /ingestion/run |
| 3 | POST /mappings; GET/PATCH /mappings/{id}; GET /lineage; PATCH /tables/{id}/layer |
| 4 | POST /reconciliation/run; GET /reconciliation/runs/{id}; GET /reconciliation/runs/{id}/results and /differences |
| 5 | GET /tables/{id}/records; GET /reports/trends; GET /reconciliation/runs/{id}/export?format=csv\|xlsx |

DDL preview will use typed, validated column definitions and dialect-aware quoting; execution must require engineer/admin permission and explicit confirmed preview. The backend will never expose unrestricted SQL. Test credentials will be supplied only to POST endpoints over HTTPS, encrypted server-side, and redacted from all serializers. REST endpoints will require authorized host allowlists and bounded responses. Future jobs return run IDs, with progress read through execution endpoints; no arbitrary background scripts.
