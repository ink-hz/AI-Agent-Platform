# DAQ admin observability integration

> REQUIRED SUB-SKILL: subagent-driven-development. Continue without per-task user confirmation; user previously authorized completing release corrections and forbids changing the camera repository.

Goal: `/admin`, `/admin/agents` and `/admin/sessions` consume the real DAQ service's operational state, sessions and trace instead of merely offering its external login card.

Observed baseline: production DAQ has12 persisted sessions; cloud replica has0 DAQ sessions. Catalog/profile already declares `ai-daq-fae-agent`; existing remote ingestion config only handles camera FAE and Office. Cloud admin API image21d82d6, current source pointer2c6111b. Local master21a789b8 clean. Local remote-sync and cloud-sync launchjobs both show last exit1. Diagnose those errors separately; never conceal them as fresh data.

## Contract / constraints

- DAQ remains independent `ai-daq-fae-agent`, schema`daq_fae`, container`ai-daq-fae-backend`, DSN from that container's `DAQ_DATABASE_URL`; no camera code/container/config/knowledge change.
- Extend the existing source mirror, normalization, sanitizer, signed/encrypted replica and observability repository chain. Do not bypass admin authentication, fabricate sessions, directly inject unreviewed replica rows, silently relabel DAQ as camera, or create a one-time import with no incremental continuation.
- Transport/source-sync identity may be `daq`; normalized `source_kind=fae` denotes the existing FAE runtime family, while `agent_id=ai-daq-fae-agent` and `session_key`/turn keys under`daq:` must remain distinct. Document this distinction; no new UI-only source-kind is required. Keep current camera semantics and source keys unchanged.
- If existing contracts require additional typed source identity, adjust them explicitly with adjacent negatives rather than skipping gates. Use schema/table allowlists and read-only source transactions. Preserve external session IDs, real trace IDs, source/version metadata, stages, fallback and outcome.
- Session sender identity must come from verified persisted stable ownership bindings, never display-name guessing. Missing bindings remain unavailable.
- Existing user-facing DAQ card must not be equated with runtime registration or data ingestion. Add reviewed runtime registration and safe health observation without enabling production replay.
- Tests are local synthetic data/isolated Postgres only. No real model calls, production test questions, business messages or browser QA. Reuse passed unaffected evidence. Production source observation is read-only; only controlled synchronization may write management mirrors/replicas under existing governance.

## Task1: producer-to-admin data contract

Implement source config/export/parser/import, dedicated DAQ mirror and additive normalized read views, plus required sync status types. Add runtime registration and zero-session source category handling where needed. Preserve old FAE/Admin imports. Write meaningful failing regressions for DAQ UUID/FK isolation, source authenticity, source errors, trace roots and new-schema permissions. Use current repository tests/fixtures; verify affected contracts once. Commit source and migration as a cohesive change. Report exact design choices and read-view dependencies before changing code.

## Task2: operating synchronization

Root diagnoses existing failed launchjobs without printing secrets/personal records, fixes only verified blockers, applies reviewed local mirror migration, runs the affected DAQ ingestion and the existing signed/encrypted cloud export/import. Preserve secret files, source ownership, queues and prior state; keep rollback snapshots. Prove initial synchronization and subsequent incremental/idempotent behavior through counts/keys, not fabricated data.

## Task3: independent review, deployment and records

Independent code/contract review before deployment; root verifies real admin repositories/APIs for DAQ Agent, session count, one existing session's trace consistency and healthy runtime. No production model/QA calls. If cloud API changes are needed, rebuild only the reviewed component with exact previous identities and rollback. Preserve camera/DAQ answer container identities. Record actual pushed/deployed versions, read-only findings and any missing signed-in UI coverage; merge to clean mainline without overwriting unrelated work.
