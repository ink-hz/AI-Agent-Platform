# Platform erasure local evidence

No production connection, deployment, object deletion, or queue reset was performed.

## Signed-in platform regression

Command (from `backend/`):

```text
.venv/bin/python -m pytest -q tests/test_conversation_attachment_migration.py -k 'legacy_composite_expansion'
```

Result: `2 passed, 33 deselected in 1.47s`.

The single-job test asserts that legacy composite expansion returns the queued job id with a null attachment id and commits one running claim. The two-job test asserts that the returned job id and attachment id belong to different rows and that both rows are claimed by the one statement.

## Full read-only runbook SQL

A temporary pytest harness used the existing platform control PostgreSQL fixture and invoked `psql -X -v ON_ERROR_STOP=1 -f docs/runbooks/2026-09-11-platform-erasure-readonly.sql` twice:

1. With migration 100 applied: all 81 SQL-file lines executed; output had 31 lines; the recorded SHA-256 matched `15355874fce1ea58d00056eb07233a0fb3ef4a3cd7e807ea6e6608deb3668177`; required privilege checks were true.
2. With the version-100 receipt temporarily removed and its six grants revoked: all SQL-file lines executed; output had 30 lines; no version-100 receipt was returned and all six privilege checks were false.

The harness restored the receipt and grants in `finally`. Result: `1 passed in 1.45s`. Pytest emitted 30 unknown-marker warnings because the temporary test lived outside the repository pytest configuration; they do not indicate SQL failures. The concise captured output is in `platform-erasure-readonly-runbook.log`.

## Hotfix patch

`docs/runbooks/2026-09-11-platform-erasure-hotfix.patch` was generated with:

```text
git diff --binary master -- backend/app/attachments/erasure.py backend/control_migrations/100_attachment_erasure_worker_access.sql
```

It contains only the one-evaluation FROM query change and public migration 100. It has not been applied to master or deployed.
