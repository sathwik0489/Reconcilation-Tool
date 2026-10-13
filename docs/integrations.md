# External integrations and enterprise UI — current release

This document supersedes the earlier Phase 1 limitations in the original foundation architecture/API/verification documents. The foundation's authentication, account roles, audit history, registered tables, and Medallion schema are retained. No existing user password is reset by migrations.

## Supported, functional connectors

| Connector | Authentication | Discovery/records | Destination support |
|---|---|---|---|
| Generic REST | Public resource, API key header, issued bearer token, HTTPS Basic when no official OAuth flow exists, OAuth 2.0 code + PKCE | User-declared exposed paths; configured JSON record path; none/offset/page/cursor pagination; declared query filters | PostgreSQL full/incremental staging, direct bounded reconciliation |
| PostgreSQL | Database username/password encrypted server-side; TLS verify-full by default | Actual authorized-schema table and column inspection; bounded real SELECT queries | Table creation/registration, data staging, direct comparisons |
| ServiceNow | Official OAuth flow or an issued bearer token; no provider password form | Official `/api/now/table/{resource}` interface; `result`, `sysparm_limit`, `sysparm_offset`, permitted `sysparm_query`/`sysparm_fields` | Same staging/reconciliation path as generic REST |
| Secure CSV | Authenticated engineer/admin upload | UTF-8 parsing, unique safe column names, row-shape validation, 5 MB/row bounds; encrypted server storage | Full PostgreSQL ingestion and subsequent mapping/comparison |

Salesforce, Dynamics 365, SAP, Oracle ERP, and Workday cards are clearly marked planned. Generic REST can use their documented endpoints when those endpoints and authentication settings are actually authorized; the platform does not claim to discover provider-internal databases. There are no mock production connections, embedded datasets, or simulated results. ServiceNow implementation follows its [official REST documentation](https://www.servicenow.com/docs/r/api-reference/rest-api-explorer/c_RESTAPI.html).

## Server authorization and setup

Add exact permitted API, OAuth, and database hosts to the project's private `.env`, then restart FastAPI:

```dotenv
INTEGRATION_ALLOWED_HOSTS=api.company.com,login.company.com,db.company.com
INTEGRATION_PRIVATE_HOSTS=db.company.com
FRONTEND_PUBLIC_URL=http://localhost:3000
INTEGRATION_MAX_ROWS=10000
```

Empty allowlists deny all external network connections. Private addresses require both lists; cloud instance metadata, unspecified, and multicast addresses are denied. REST requests require HTTPS (443/8443), pin a validated DNS address, preserve the official Host header/TLS SNI, verify certificates, disable redirects and environment proxies, and bound response sizes/retries. This follows [HTTPX's documented SNI extension](https://www.python-httpx.org/advanced/extensions/). Connection creation verifies these host rules before credentials are sent.

PostgreSQL connections pin hostaddr and require TLS; use a **separate data database** and an explicitly authorized existing schema. The application metadata database name is blocked as a data destination. Use source accounts with SELECT only and target accounts limited to the approved schema. Grant CREATE only when table creation is required. The isolated native metadata cluster is not a TLS-enabled data connector demonstration database.

Use README's Windows/macOS setup. Existing installations: install updated backend requirements, run `alembic upgrade head`, run `npm ci`/`npm run build`, restart services. Current migration head is `0003_files`; the original 18 metadata tables remain, seven integration metadata tables are added, and the connector catalog is seeded. Docker includes a persistent authorized-files volume; the supplied definitions still require actual Docker validation.

## Authorization workflow

1. Open Integrations or Data Connections. Choose a supported connector and configure its official exposed interfaces.
2. Save the encrypted configuration. Credentials/client secrets are never included in response serializers, search results, audit fields, or application logs.
3. OAuth users click Authorize, sign in on the provider, and grant scopes. Register the exact callback `FRONTEND_PUBLIC_URL/api/integrations/oauth/callback` with the provider.
4. The server stores a hashed short-lived single-use state and encrypted PKCE verifier, exchanges the code server-side, validates granted scopes, stores encrypted access/refresh tokens, and tracks expiry. Refresh is serialized through the auth row lock and uses the configured token endpoint.
5. Test connection. PostgreSQL discovers tables in the approved schema. Generic REST and ServiceNow test the first declared exposed resource; they do not assume a universal provider discovery API.
6. Select a resource, preview permitted records, inspect types, and approve the source schema to register it. Changes are reported against the last approved snapshot.
7. Select an owned PostgreSQL destination, columns, layer, and ingestion mode. Confirm authorized row changes before running.

OAuth implementation follows [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html) and [PKCE RFC 7636](https://www.rfc-editor.org/info/rfc7636/). Identity-provider OIDC/SAML **application sign-in** is distinct from authorizing a data integration and is not implemented by this release. A future compatible enterprise IdP configuration must specify issuer, trusted keys, audience, exact redirect URIs, and role mapping; no password-grant/federated-login imitation is included.

DELETE integration performs local revocation, clears encrypted auth material and pending states, preserves execution metadata, and requests configured provider token revocation. The returned result explicitly identifies unconfigured/failed remote revocation. Provider portal revocation remains necessary when remote revocation was not confirmed. Retained encrypted file records are inaccessible through revoked integrations; operational retention/deletion policy is required for storage.

## Ingestion behavior and limits

Full ingestion creates a new destination table or transactionally replaces rows in an existing authorized table. Selected values are converted to destination SQLAlchemy types; unsupported values fail. Incremental runs append records strictly after a saved configurable watermark. Reuse the returned sync configuration ID; its destination/columns/rules are immutable, and the watermark advances only after successful destination commit. Watermarks must be monotonically increasing nonnull numeric values or consistently ordered timestamp/text values. Late-arriving records at or below a saved watermark require a full resync; compound/tie-aware provider change tracking is not included.

The initial worker is synchronous and bounded: 10,000 configurable records (server limit up to 100,000), 100 pages, 5 MB per HTTP response, 25 MB per resource read, 60-second pagination budget, 15-second request timeout, 5-second connect timeout, at most 3 attempts, and bounded Retry-After handling. Oversized/unfinished reads fail before writes. There is no silent truncation on ingestion or reconciliation. Long-running distributed scheduling and automatic webhook-triggered ETL are not enabled.

Source data remains in authorized external systems or encrypted configured `DATASET_STORAGE`; it is not copied into metadata tables. PostgreSQL writes occur in an external database transaction with a per-table advisory lock. Metadata and destination use separate transactions. Incremental runs write an authorized-schema `_reconcile_sync_ledger` watermark in the same destination transaction as inserted rows; reruns with the same configuration ID read that ledger to recover even if the central watermark is stale. This bookkeeping table is excluded from resource discovery and requires CREATE permission on first incremental use. Full reruns replace rows naturally. Central execution status can still lag after a metadata commit failure, so operational recovery tooling and production failure-injection testing remain necessary; this is not a general distributed exactly-once guarantee.

CSV files are parsed from content, not trusted just by extension. Unsafe/duplicate names, incorrect row shape, invalid UTF-8, oversized files, and excessive rows are rejected. UUID storage names prevent supplied-path traversal. Files are encrypted with the server's configured Fernet key; protect and back up that key and storage. Excel/Parquet parsers are not enabled yet. CSV records are treated as text; existing target column types determine safe conversion.

## Mapping and reconciliation

Approve/register source resources and target tables first. Choose source tables, target branches, column pairs, composite business keys, case/null handling, numeric/timestamp tolerance, non-null columns, typed filters, joins, and COUNT/SUM/MIN/MAX/AVG aggregations through forms. React Flow visualizes selected source/target edges, supports moving nodes and connecting selected nodes, and is paired with the same touch-friendly form. Saving creates a new version/configuration; editing the selected form then using Save as new mapping preserves the old execution history rather than mutating previous runs.

One-to-many uses the same source on each independent target branch. Many-to-one joins additional sources to earlier sources using configured column equality; left joins retain primary-source records. Duplicate business keys are reported instead of silently collapsed. Source grain can differ by branch through aggregation over mapped business keys. Column transformations are allowlisted exact/trim/lower/upper operations; arbitrary SQL/Python is never executed. Keys normalize compatible numeric values while preserving leading-zero text IDs; case-insensitive matching can normalize text keys. Timestamps and numeric values support configured tolerances.

Each run saves a configuration snapshot, per-target counts, match percentages, rule results, and column/key differences. Overall source coverage uses source identities retained through filters/joins/aggregation; it does not sum repeated fan-out record counts. An overall matched record must match every relevant target branch. The dashboard's historical counters explicitly remain *latest successful table comparisons* per job; the results screen separately labels unique-source coverage.

Detailed differences are paginated, clickable for side-by-side records, and exportable as CSV. Branches with more than 10,000 differences fail with a narrowing instruction; CSV exports above 50,000 differences fail rather than silently truncate. Missing/extra/duplicate/null/value mismatches are distinguishable using labels. Schema summaries check mapped target columns, not a full provider type-equivalence engine. SQL Server/MySQL adapters, Excel export, richer schema/type rules, dataset-wide search/filter/compare, and large-scale execution remain further work.

## Signed events

Optional webhook signing secrets enable `POST /api/integrations/{id}/webhook`. Sign `timestamp + '.' + raw_body` with HMAC-SHA256 and send `X-Reconcile-Timestamp` and `X-Reconcile-Signature`. Requests must be within five minutes, <=64 KB, and signed correctly; the same event hash is deduplicated under a connection lock. Only event identity is retained, not arbitrary payloads. This verifies the generic protocol and records notifications; provider-specific signatures/subscriptions and automatic sync are not advertised.

## API additions

All requested integration endpoints are implemented: list/create/detail, authorize/test, resources/schema/preview, ingest, sync-history, revoke. Additional endpoints include `/integrations/catalog`, OAuth callback, resource schema approval, signed webhook, secure CSV ingestion, global `/search`, `/notifications`, table DDL preview/create/register/layer/preview, `/workflows/tables`, mapping create/detail, reconciliation run/detail/results/differences/CSV export. Existing API/session/preferences/inventories remain functional. Engineer/admin roles are required for writes. Owner checks protect executable integrations/resources; admins have workspace-wide administration. OpenAPI provides the exact request schemas at `/api/docs` in development.

## Enterprise interface

Navy #14213D sidebar, white workspace, Teal #00B8A9 primary buttons with navy text, neutral #F8FAFC cards/background accents and #1E293B text. Responsive navigation, branded PWA assets, reusable forms, catalog cards, global search, audited notifications, account profile/settings, live metrics/recent connections/tables, comparison chart, execution history and record drill-downs are integrated. No third brand accent is intentionally introduced. Physical-device installation/access still requires HTTPS deployment.

## Verification limits

Automated tests use real PostgreSQL schemas/destination tables with isolated test identities. External provider HTTP responses and TLS connector injection are contract fixtures in tests only; production adapters always use real authorized network requests. No live provider credentials/tenant were supplied, so live Salesforce/ServiceNow/OAuth tenant acceptance is not verified. Chromium/WebKit phone/tablet emulation is not physical Windows/macOS/iOS/Safari/Edge release verification. Docker, load testing, full accessibility audit, encrypted backup/rotation, distributed rate limiting, idempotent ingestion recovery, and production deployment remain required release gates.
