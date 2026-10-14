# Organizations and roles

Reconviora is multi-tenant. An organization owns its connections, encrypted credentials, registered tables, ingestion history, mappings, reconciliation runs, results and audit trail. Nothing is shared between organizations.

## How isolation works

- `organizations` and `organization_memberships` hold tenants and who belongs to them. A membership carries the role.
- Connections, tables, ingestion configurations and runs, mappings, jobs, reconciliation runs, integration connections and audit entries carry an `organization_id`. Child rows (columns, resources, results, differences, sync runs, errors) are reached only through those parents.
- The session cookie names the organization. Each request reloads the membership, so removing a membership ends that session, and a role change applies on the next request.
- Every list, search, dashboard figure and detail endpoint filters on the session's organization. A record in another organization answers 404, the same as one that does not exist.
- Connection names are unique per organization, so two organizations may use the same name.

## Roles

| Role | Can do |
|---|---|
| Organization administrator (`admin`) | Everything below, plus create accounts and rename the organization |
| Data engineer (`engineer`) | Manage connections, ingestion, tables and layers; save mappings; run reconciliations |
| Data analyst (`analyst`) | Save mappings, run reconciliations, review and export results |
| Read-only viewer (`viewer`) | View connections, tables, previews and results; save personal preferences |

Members of an organization share its connections: an analyst can run a mapping over a connection an engineer created, using the stored credentials on the server. Credentials are never returned to any browser.

## Provisioning

`python -m app.bootstrap` creates the first organization and its administrator. Administrators add members from Settings; a new account joins the administrator's organization. An email address can be registered once, so an existing account cannot yet be invited into a second organization, and there is no organization switcher. Self-service sign-up, SSO, MFA and password reset are not implemented.

## Upgrading an existing installation

Stop the API, run `alembic upgrade head`, then restart it. Migration `0004_organizations` creates a "Default organization", gives every existing account a membership with its current role, and assigns all existing records to that organization. Existing sessions end, so everyone signs in again. An administrator can rename the organization in Settings. `alembic downgrade 0003_files` restores the previous layout; analysts become viewers there.