# Verified FAE subject replica implementation — 2026-10-23

Scope: Task 3 from `.superpowers/sdd/verified-subject-brief.md`. No FAE repository, partner integration, route authorization, control migration, or current design/plan files changed by this task. The report path was checked with `git ls-files` and did not exist before this write.

## Implemented boundary

- FAE session import derives `verified_subject` exclusively from exported server `owner_subject_id` / `owner_subject_type` and persisted `metadata.authentication_mode`. The actual FAE internal identity column is `user_id`: enterprise ownership must match it; partner ownership requires it to be null. A separately supplied top-level `internal_user_id` must agree if present. Malformed, partial, mismatched ownership raises `ImportValidationError("fae_session_owner_invalid")`.
- Incoming metadata/root `verified_subject` claims are removed before the authoritative reference is added. Legacy unowned FAE rows remain unverified. Owner columns remain in imported details. DAQ/Admin import behavior remains unchanged.
- Cloud SQL projects only `details->'verified_subject' AS verified_subject`, never the details object. A validated immutable typed reference crosses RawSession/sanitized record; sanitizer and store require `agent_id=ai-fae-agent`, `source_kind=fae`. Arbitrary metadata never enters the cloud payload.
- Writer default remains the exact old session shape and previous HMAC grouping. `ReplicaExporter(..., include_verified_subject=True)` and `platform-cloud-replica export --include-verified-subject` explicitly enable the reference. Python writer APIs reject truthy strings, numbers, and null instead of coercing them. Enabled records with a subject HMAC the typed verified identity, preserving grouping across different sessions/channel identities while separating different people with the same nickname.
- The reference is stored inside the complete AES-GCM encrypted display payload; no new plaintext index column, migration, or secret/channel ID export was added. Reader/store accept exactly the legacy root shape or that shape plus a valid canonical UUID / allowed subject type reference. Unknown root/reference fields, null references, malformed references, and references on other agents/sources fail closed.
- Protected observability SessionSummary/SessionDetail and TurnDetail expose only the typed opaque reference. Existing protected routes and audits are unchanged. Identity details/names are resolved by the separate audited owner lookup from the integration task; this task performs no identity/name inference, name cache access, or network lookup. Revocation does not erase historical UUID correlation.

## Verification

TDD RED was observed before implementation: initial focused run had 33 expected failures / 11 preexisting-boundary passes; reader/CLI additions then had 5 expected failures / 1 default-path pass. A later malformed in-memory reference test reproduced two AttributeError failures before explicit typed validation was added.

Final command (using the original backend virtualenv and `PYTHONPATH=backend`):

```sh
PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q \
  backend/tests/test_sync_importer.py backend/tests/test_cloud_source.py \
  backend/tests/test_cloud_sanitizer.py backend/tests/test_cloud_exporter.py \
  backend/tests/test_cloud_store.py backend/tests/test_cloud_repository.py \
  backend/tests/test_cloud_cli.py backend/tests/test_fae_verified_subject_replica.py --tb=short
```

Result: **167 passed, 0 failed** (59 new focused cases). Existing source SQL boundary test was narrowly updated, with parent approval, to permit only the explicit verified-subject JSON projection while continuing to forbid raw details/metadata export. New focused tests pass Ruff; `git diff --check` is clean.

The full pipeline test uses actual normalization/source assembly, sanitization, signed batch encode/verification, AES-GCM store preparation, encrypted reader validation and observability projections, with local synthetic data. There are no model, production, browser, or network calls.

## Rollout / limits

Deploy the compatible reader/store before enabling `--include-verified-subject` on writers. The flag is intentionally not enabled in a production unit/config by this implementation. Default writers can continue feeding old readers.

Previously imported FAE rows need a normal source re-import to gain the derived reference; old encrypted records remain readable and remain unverified until re-exported with the explicit writer option. Unowned legacy rows cannot be attributed from nicknames. No bulk backfill, name enrichment/interface factory, PostgreSQL view migration, live database test, production deployment, or end-user browser acceptance was performed. The separate integration task owns the admin identity lookup and HTTP authorization integration.
