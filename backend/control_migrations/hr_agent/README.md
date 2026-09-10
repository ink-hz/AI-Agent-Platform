# HR Agent runtime migration

Run the root `backend/control_migrations` directory first, then explicitly select
`backend/control_migrations/hr_agent` with the existing control migrator. Both
use `platform_control.schema_migrations`; this is not an independent ledger.
Versions 089–095 in other directories are not prerequisites.

096 was checked against all 85 locally available branch/remote references on
2026-09-10; none contained 096. No network or deployed database was consulted.
Deployment must recheck its own inventory before applying this migration.

The application and Worker receive SELECT/INSERT and only mutable-table UPDATE
privileges. They receive no CREATE, DELETE, TRUNCATE or role membership. Inputs,
entries, source edges, events, result/standard revisions and budget extensions
are append-only. Owner/work composite foreign keys preserve private ownership.
Readiness only reads relation identities and the migration ledger; enabling HR
never runs DDL. Migration installation and runtime enablement are separate.
