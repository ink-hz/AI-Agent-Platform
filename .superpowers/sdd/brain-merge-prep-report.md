# Agent Brain merge-preparation report

Date: 2026-08-23

## Scope

- Worktree: `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/agent-public-entry`
- Branch: `feat/agent-public-entry`
- Integration commit: `3c4c18fa72d69b0c6ff448aa6280397a886310b1`
- Parents:
  - feature: `3201bb35bd8ebacebfeb974c6801ce2d18c8a7bf`
  - `origin/master`: `627c364d4307f5ea65d61adc0b342457fa16f50a`
- No deployment or push was performed.

## Migration collision resolution

`origin/master` remains authoritative for migration 027:

- `027_account_department_projection.sql`
- SHA-256: `531d5b31b615bec5b17860816ff955a927bdfe4c5010909c9ab9b750a1d11fc3`
- Verified byte-identical to `origin/master` using `git show ... | cmp`.

The feature migration chain was moved without gaps:

1. `027_execution_relay.sql` -> `028_execution_relay.sql`
2. `028_agent_brain_mvp.sql` -> `029_agent_brain_mvp.sql`
3. `029_agent_brain_orchestration.sql` -> `030_agent_brain_orchestration.sql`
4. `030_execution_stop_delivery.sql` -> `031_execution_stop_delivery.sql`
5. `031_content_key_canaries.sql` -> `032_content_key_canaries.sql`

All versioned SQL function/constraint/index names, application calls, tests,
implementation plans, and rollback documentation were advanced with their
owning migration. Task 9 now protects migration 032 as the latest Agent Brain
migration, while the Mission run-table test correctly inspects migration 029,
where that table is created.

## TDD evidence

RED was observed before renaming:

```text
tests/test_control_plane_migration.py::test_first_control_migration_exists
AssertionError: missing execution relay migration: .../028_execution_relay.sql
```

Focused GREEN:

```text
358 passed in 71.30s
```

This focused gate included the real PostgreSQL control migration chain,
execution-relay migrations/repository/auth/deployment contracts, Agent Brain
migration/authorization/deployment contracts, checksum guards, and both
production and preview role boundaries.

## Full verification

- Backend: `2280 passed, 1 skipped, 92 warnings in 107.81s`
- Frontend: `40 passed` files, `311 passed` tests
- Production frontend build: succeeded (`1347 modules transformed`)
- Shell syntax: `bash -n deploy/cloud/*.sh deploy/local-execution-worker/*.sh`
- Diff hygiene: `git diff --check` and `git diff --cached --check`
- Migration 027 byte identity: passed via `cmp`
- Branch relationship after integration: `99 0` for
  `git rev-list --left-right --count HEAD...origin/master`

Warnings are existing Starlette/httpx deprecations and test-environment
`scrollTo` notices; there were no test failures.

## Independent review

The first independent pass reported `0 Critical / 1 Important / 1 Minor`:

- one relay-plan paragraph still said `migration-027`;
- the Agent Brain migration test constant still used the name
  `V28_FUNCTIONS` for migration-029 functions.

Both references were corrected, and the real PostgreSQL Agent Brain migration
suite passed again (`11 passed`). The same reviewer then returned the final
verdict: `0 Critical / 0 Important / 0 Minor`.

## Preserved user-owned files

The following pre-existing dirty reports were neither edited for this task nor
staged in the integration commit:

- `.superpowers/sdd/task-2-report.md`
- `.superpowers/sdd/task-3-report.md`
- `.superpowers/sdd/task-4-report.md`
- `.superpowers/sdd/task-6-report.md`
