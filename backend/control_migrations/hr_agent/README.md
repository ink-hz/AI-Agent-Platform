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
Readiness only reads relation identities, the migration ledger and effective privileges; enabling HR
never runs DDL. Migration installation and runtime enablement are separate.

C adds 098 material authorization proofs and 099 encrypted candidate intake.
Root migration 100 grants the narrowly required upload-version columns to the
matching maintenance role for source erasure. HR readiness requires 096–100
checksums and the effective maintenance privileges; apply the root directory
(including 100) before the HR opt-in directory. No legacy HR candidate data is
automatically copied by these migrations.
