# Connector boundary (Phase 2)

Implement a registry of typed adapters: test, list_schemas, list_tables, inspect_columns, preview, create_table, stream_read, and write_batch. PostgreSQL, SQL Server, MySQL, CSV, Excel, Parquet, and authorized REST adapters share this boundary. File adapters use explicitly authorized storage; they do not store dataset contents in metadata tables.

Generate identifiers from inspected/validated metadata and quote them with each SQLAlchemy dialect. Parameterize values. Never accept arbitrary user SQL. Restrict external hosts and REST redirects using a deployment allowlist to prevent SSRF; use timeouts, read limits, TLS, and restricted database credentials.

No connector execution is enabled in Phase 1.
