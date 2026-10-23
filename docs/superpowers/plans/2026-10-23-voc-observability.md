# VOC observability implementation plan

> Execute inline in dependency order, with test-first changes and independent
> review before production activation.

**Goal:** Show real VOC bot conversations in the admin overview, agent directory
and session replay through the same existing observability pipeline.

**Architecture:** Add a VOC producer and isolated source mirror; preserve existing
normalization, sanitization, signing, encryption, pagination and authorization.

**Tech stack:** Python, PostgreSQL, FastAPI, TypeScript/React.

## Constraints

- Follow `AGENTS.md` and the accompanying VOC observability design.
- Do not modify, stage or delete existing untracked user files.
- Use fixed read-only source SQL and persist no source credentials in exports.
- Source absence/errors never fabricate zero activity or ownership.
- Preserve the separate unresolved bot-message investigation.

## Task 1: Producer and isolated mirror

Files: `backend/app/sync_remote/voc_export.py`, `config.py`, `export.py`,
`importer.py`, `cli.py`; `backend/migrations/016_voc_observability.sql`;
`deploy/migrate-observability`; `backend/tests/test_voc_observability.py` and
`test_voc_observability_postgres.py`.

- [ ] Add failing tests for VOC configuration, snapshot identity/completeness,
  stable employee-scoped keys, excluded secrets, pending/failed deliveries,
  idempotent imports and source-isolated permissions.
- [ ] Implement `build_voc_program(source: SyncSource) -> str` and
  `validate_voc_records(output: str) -> None`. The program emits
  `source_identity`, two `table_complete` markers and allowlisted `table_row`
  records for `chat_sessions` and `chat_turns` only.
- [ ] Extend importer normalization for these two VOC tables only. Import
  supplied canonical UUID ownership in private source columns; discard
  metadata-only ownership claims and leave the FAE-scoped wire proof absent.
  VOC has no review-backfill contract.
- [ ] Add migration 016 with two private mirror tables and transactionally
  append VOC to `platform_read.sessions` and `platform_read.turns`, preserving
  existing view ownership, grants, keys and rows. Authorize the migration in the
  existing checksummed runner.
- [ ] Run `python -m pytest tests/test_voc_observability.py
  tests/test_voc_observability_postgres.py tests/test_sync_remote_cli.py` from
  `backend` with the project's virtual environment. Confirm expected failures
  before implementation, then passing results before progressing.

## Task 2: Shared cloud read contract and UI source filter

Files: `backend/app/observability/models.py`, `repository.py`;
`backend/app/cloud_replica/sanitize.py`, `store.py`, `repository.py`;
`backend/app/fleet/service.py`; `webui/src/types.ts`, `sessionNavigation.ts`,
`pages/SessionsPage.tsx` and relevant source-label utilities/tests.

- [ ] Add failing tests for VOC source validation through sanitizer/store,
  agent counts, answered-turn usage totals, session listing/replay, filtering
  and missing-source freshness. Pending turns must not count as answered.
- [ ] Add `voc` to explicit source enums without aliasing it to another source.
  Classify catalog-only VOC as source `voc`; treat its snapshot as synchronized
  data for freshness. Keep all existing access controls and source keys.
- [ ] Add the VOC source option and label, preserving existing navigation and
  pagination. No new page or per-page data source is introduced.
- [ ] Run focused backend repository/replica/fleet/API tests, source navigation
  tests and the production frontend build.

## Task 3: Verify and publish

- [ ] Review the exact final diff and resolve blocking findings.
- [ ] Commit implementation to the clean tracked mainline; build an immutable
  release without existing untracked user files.
- [ ] Back up the observability source database; apply migration 016 with the
  checksummed runner. Run `python -m app.sync_remote.cli --source voc` and
  inspect counts/status only. Run it again to prove no duplicate sessions.
- [ ] Export/import through the existing signed replica pipeline and verify
  VOC totals against its source. Ensure other agents' records remain present.
- [ ] Publish the scoped Platform code/UI changes using established deployment
  controls; verify the three admin APIs and record exact running versions.
- [ ] Save production count/status evidence and any remaining unverified
  browser or employee-resend checks in a handoff.
