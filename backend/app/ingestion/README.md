# Ingestion boundary (Phase 2)

Preview → detect schema → select columns → choose destination → validate/create schema → execute full or watermark ingestion. Persist immutable configuration/run snapshots. Advance a watermark only after destination commit. Reject ambiguous watermark types and define tie handling. Secure uploads by size, type, safe names, isolated storage, and parser resource limits. The initial worker can run bounded local jobs; deployment requires a durable worker queue before long-running jobs are enabled.

No ingestion execution is enabled in Phase 1.
