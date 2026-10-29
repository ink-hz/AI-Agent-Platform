# Cloud Agent observability without the office relay

Status: approved direction in the current conversation; user requested execution.
Implementation baseline: platform master 01368dea. Production revision is checked
independently before release. Signature-400 investigation remains paused.

## Problem and acceptance

FAE/ADMIN source imports run on the office Mac daily at03:20; DAQ/VOC imports run
every300s. The Mac then exports signed cloud replicas every300s. Cloud freshness
currently uses the latest global import, hiding stale upstream agents. A real
FAE conversation has seven persisted turns while its office/cloud replicas have
zero after a session-header-only import. Its title therefore stays empty.

Move the four cloud sources' session publication onto the cloud host. Healthy
commit-to-API visibility target:10–30s at measured present workload. Retain
source IDs, canonical Session/Turn/user identities, exact outcomes, complete
answers, feedback/review semantics and access controls. Sync is asynchronous
and does not call models or alter Agent execution. Recover historical gaps and
verify the seven-turn incident by stable identity, not title or nickname.

## Architecture

Each source has an independently scheduled read-only repeatable-read snapshot
adapter. The initial implementation reads a complete bounded business snapshot,
computes canonical per-session differences, and publishes only changed records.
It does not scan raw trace JSONL on every poll. This avoids source triggers,
unsafe timestamp-only cursors and late-commit omissions. Measure this path at
current volume; a poll exceeding its10s budget blocks the latency acceptance
claim and requires a source-side committed change ledger before scale-up.

An isolated cloud sync worker polls each source every10s, with independent
failure/backoff and a per-agent publication lock. It reuses the existing raw
session model, credential scrubber, stable-ID builder and encrypted replica
payloads. A single consistent snapshot contains a session and all its turns;
source failure never replaces good data with an empty snapshot. Publication and
the agent's source-read watermark commit together. Failed attempts preserve the
last successful source watermark and expose a stable error code.

Snapshot diff is an explicit first-release implementation, not a claim of WAL
CDC. Source reads are bounded by time/body/record limits and fail visibly when
incomplete. Missing records may be deleted only from a validated complete
snapshot within the owned agent's scope. Independent workers cannot apply an
older snapshot over a newer one.

## Canonical and identity compatibility

- FAE keys remain `fae:<native UUID>`, ADMIN `admin:<native UUID>`, DAQ
  `daq:<native UUID>` and VOC its existing deterministic UUID5 projection.
- DAQ's normalized source family remains `fae`, including `user:fae` HMAC scope.
- Preserve the current FAE verified-subject opt-in and exact user-HMAC input.
  Metadata claims and display names never establish ownership.
- ADMIN identity requires the existing complete platform-session evidence;
  resolve names through a trusted current subject projection, not arbitrary
  source metadata. Identity additions must be explicitly agent-scoped and must
  not change an existing user-ID derivation.
- VOC counts only confirmed `sent` deliveries as completed answers; pending and
  failed deliveries retain their actual state.
- Do not invent a successful answer for a failed/cancelled request. Native
  processing/error records are distinguishable from completed Q/A turns.

The existing sanitizer policy preserves authorized business text and strips
credentials. Keep that policy; do not describe it as irreversible text
anonymization. Raw signatures/model requests and original attachments are not
part of this sync.

## Writer ownership and security boundary

Add DB-enforced per-agent publisher ownership in the replica. Cloud workers may
write only their assigned agent's session records/state. Legacy office imports
must acknowledge their valid signed batch while skipping cloud-owned sessions;
they must not overwrite or delete them. Office management/review projections
remain separately owned. Drain/check queued old batches during cutover, and
retain an explicit ownership rollback operation. Office exports should also
exclude cloud-owned sessions after cutover.

Keep stable identity key bytes so all existing Session/Turn/user IDs remain
unchanged. This revises the old local-only custody boundary: the key is mounted
only into the isolated cloud sync worker, never the API/frontend, and protected
by a dedicated account,0600 files/read-only mounts and no public listener.
Replica encryption keys and source credentials follow the same isolation.
Source DB roles are read-only and restricted to the required tables/views.
No business DB credentials are added to the platform API. Installation and
role/ownership changes are controlled deployment operations with backups.

## Native and local Agents

Current HR uses the independent `platform_hr_agent` service and owner-scoped
threads/works/entries; legacy `platform_control` conversations are not its
current execution source. SDK uses native requirement/task actions and durable
create intents; those are executions, not chat transcripts. Register native
source modes and correct workspace/audit links in unified observability, while
retaining their existing owner/business permissions. Do not manufacture chat
turns, feedback or reviews for these sources. Private HR content must not enter
an all-user replica without an explicit authorized observation contract.

Local MetaBot/Marketing sources retain local publication. Their source mode and
source observation times stay distinguishable from cloud direct/native sources.
No stale catalog entry is treated as proof of a currently running instance.

## API and UI

Expose per-agent mode, last successful source read, replica publication, last
failure, stable error code and actual lag. Session/Agent freshness uses that
agent's successful source observation, never another writer's global heartbeat.
Legacy records with no proven source watermark are not silently marked current.
The list and replay refresh while visible, preserve existing content during a
refresh, cancel stale requests and stop polling when hidden/unmounted. Refresh
errors retain visible data and disclose staleness. Existing auth remains.

## Validation and rollout

Use synthetic source data and disposable PostgreSQL for producer→adapter→cloud
store→HTTP consumer tests. Cover four source families, identity compatibility,
late commits, answer/feedback/review-only updates, unchanged sessions, deletes,
restart/replay, source outage, cross-agent isolation and old-writer overwrite.
No model evals or synthetic business writes against production.

Baseline focused92 tests passed before implementation. New behavior follows
red/green tests and independent task/branch review. Stage release from the exact
merged master revision, preserve concurrent security changes, take encrypted
DB/config backups, deploy worker disabled, validate permissions, then cut over
one agent at a time. Compare real source/cloud session and turn counts, titles,
identity, outcomes and stable keys. Recover the seven-turn incident. Verify
each source advances independently and measure real commit-to-visible lag
without generating production model requests. Browser QA belongs to the user.
