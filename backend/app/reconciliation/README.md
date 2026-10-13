# Engine boundary (Phase 4)

Compile structured rules into bounded adapter queries. Materialize transformed/filtered source and target views at each branch's declared grain. Reject nonunique business keys for deterministic value comparison; report duplicates separately. Composite keys need typed, unambiguous encodings and explicit null semantics.

Each mapping edge executes independently, with typed filters, join predicates, column transforms, aggregation, numeric/timestamp tolerance, and null behavior. Many-to-one branches join their mapping_sources before comparison. Persist configuration snapshots and per-edge rule results. Overall source coverage must use the union of original source identities across edges; do not sum fan-out target counts. Dashboard Phase 1 counts explicitly represent table comparisons only.

No reconciliation execution is enabled in Phase 1.
