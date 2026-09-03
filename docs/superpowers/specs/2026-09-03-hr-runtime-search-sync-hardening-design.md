# HR Runtime, Search, and Sync Hardening Design

## Context

The HR business flow exposed three independent reliability failures on
2026-09-02 and 2026-09-03:

1. Claude Code returned HTTP 400 `tool use concurrency issues` after a
   tool-heavy session was compacted. Resetting the poisoned session recovered
   later text-only work, but a new nine-image session reproduced the same 400
   after Claude issued nine `Read` calls in parallel.
2. The shared web-research sidecar returned `SEARCH_UNAVAILABLE`. Its first
   failing search exhausted the 180-second deadline, while later health checks
   failed because the sidecar required an exact global Codex CLI version. The
   sidecar was updated for `0.150.0`, but the machine-wide executable has since
   moved to `0.153.0`, making health fail again without proving that search is
   broken.
3. The daily AI ADMIN observability import has failed since 2026-08-29. A
   diagnostic import reproduced `UndefinedTable` for
   `platform_identity.session_subject_links`: importer code from commit
   `812081b6` is live locally, but migration
   `backend/migrations/011_admin_session_subject_links.sql` was not applied to
   the local observability database.

The five-minute sanitized cloud-replica pipeline is a separate path and is
healthy. HR data reached cloud generation `5795`; the most recent affected HR
session has `last_active_at=2026-09-03T19:41:45+08:00` and
`expires_at=2027-09-03T19:41:45+08:00`. The cloud replica therefore does not
need a speculative rewrite. It needs end-to-end acceptance after the local
fixes.

## Goals

- Prevent incompatible parallel Claude tool calls before they can corrupt a
  new or resumed HR session.
- Recover one safe attempt from provider/session corruption without duplicate
  external actions, duplicate business replies, or an infinite loop.
- Make shared web research independent of mutable machine-global Codex CLI
  upgrades and recover once from a transient provider failure within a fixed
  deadline.
- Restore AI ADMIN observability imports and prevent code/schema drift from
  reaching a running Platform release again.
- Render a failed Agent turn as a failed turn, not as an apparently missing
  answer.
- Prove Flywheel, cloud replica, Feishu delivery, and administration views agree
  on the final outcome.

## Non-goals

- Guarantee that external model, network, Feishu, or search-provider services
  can never be unavailable.
- Replay a turn after an external side effect or after a usable terminal answer
  exists.
- Add an unapproved search provider, scrape an unofficial search-result page,
  or fabricate current recruitment information.
- Rewrite old failed turns. Historical failures remain immutable evidence.
- Change shared Nginx routing or restart unrelated bots and applications.

## Considered approaches

### A. Prevention plus bounded recovery (selected)

Disable parallel tool use in the compatibility gateway, retain a single safe
replay budget, pin the search provider toolchain, restore the missing database
migration, and add deployment gates. This removes the observed triggers and
still handles a bounded provider failure.

The trade-off is that tool-heavy HR tasks perform independent reads
sequentially and can take longer. Stability is preferred over parallel-tool
throughput for this business bot.

### B. Recovery only

Allow every `tool use concurrency issues` response to start another session.
This is rejected because a fresh session can deterministically issue the same
parallel calls and fail again. It also risks stacked retries across the current
provider, session, context, and process-exit recovery branches.

### C. Upgrade only

Upgrade Claude Code and Codex CLI globally. This is rejected as the primary
repair because the Claude failure is a published long-session/tool-history
class of bug, while global Codex upgrades are themselves what invalidate the
web-research exact-version gate. Global upgrades also affect bots outside the
authorized HR scope.

## 1. Claude tool-call prevention

The MetaBot Claude compatibility profile gains an immutable
`disableParallelToolUse: true` capability. The loopback Messages API adapter
applies it to every `/v1/messages` request that contains client tools:

- if `tool_choice` is absent, inject
  `{ "type": "auto", "disable_parallel_tool_use": true }`;
- if `tool_choice.type` is `auto`, `any`, or `tool`, preserve all existing
  fields and set `disable_parallel_tool_use: true`;
- if `tool_choice.type` is `none`, leave it unchanged;
- requests without tools and non-Messages routes remain byte-for-byte
  semantically unchanged;
- image promotion still runs before forwarding, and the adapter recalculates
  `content-length` after both transformations.

This is the prevention layer. It ensures Claude returns at most one client tool
call per model response, so nine images are read across nine valid protocol
round trips rather than one unsupported parallel result group.

The adapter logs only the boolean policy and counts. It does not log prompts,
credentials, image bytes, or transformed request bodies.

## 2. One recovery budget per Agent turn

The Feishu stream path, Feishu exception path, API Task stream path, and API
Task exception path share one recovery-attempt state. The state begins at zero
and increments before any replay. Provider, process-exit, context-overflow, and
session-corruption policies all consume the same budget.

A replay is permitted only when all of these are true:

- recovery count is zero;
- the execution was not cancelled;
- no external-side-effect tool was observed;
- no usable terminal answer was observed;
- the error is one of the explicitly recoverable classes.

`tool use concurrency issues`, unmatched/duplicate tool results, and corrupt
session history are recoverable whether the failed attempt was resumed or
fresh. A resumed-session failure clears only that chat's mapping. A fresh
failure still tears down its executor before replay so the corrupt transcript
cannot be reused. The replay starts a new Claude session under the serial-tool
policy.

The second failure is terminal. Recovery policies may not chain and may not
reset the counter. A cancellation, write tool, message send, scheduling action,
or usable answer prevents replay.

The original attempt remains observable as a failed attempt/trace event. The
user turn receives one terminal outcome and at most one final Feishu delivery;
historical failed turns already stored in Flywheel are never overwritten.

## 3. Web-research provider isolation

The shared web-research service stops executing
`/opt/homebrew/bin/codex`. Deployment installs a validated private Codex
`0.153.0` toolchain under:

`/Users/agentops/AgentRuntime/web-research/toolchains/codex/0.153.0/`

The active service receives an absolute executable path inside that immutable
toolchain. Toolchains are separate from source releases, are not copied into
every release, and retain only current plus two rollback versions. The
machine-global Codex may upgrade without changing service health or behavior.

Deployment requires all of the following before activation:

- private executable reports exactly `codex-cli 0.153.0`;
- authentication reports ChatGPT or API-key login without exposing tokens;
- offline contract tests pass;
- one live health call, one HR-scoped search, and one fetch pass;
- the existing MetaBot process identities remain unchanged.

Runtime retry remains bounded by the existing 180-second request deadline. A
transient transport/process timeout receives at most one retry with jitter and
remaining-budget enforcement. Authentication, invalid request, invalid output,
and policy errors do not retry. The circuit breaker admits one half-open probe
after cooldown and closes only after a real successful provider call. Agent
prompts must not implement their own 90/240-second sleep loops.

If both attempts fail, the bot reports a current-data lookup failure rather
than inventing results. This design improves availability but does not claim an
external provider can never fail.

## 4. Platform schema and sync repair

Apply the existing idempotent migration
`backend/migrations/011_admin_session_subject_links.sql` to the local
observability database using the repository migration runner. Do not manually
create an ad-hoc table.

The synchronization release gate must then prove:

- the migration ledger contains migration 011;
- `to_regclass('platform_identity.session_subject_links')` is non-null;
- the sync writer has only the intended privileges;
- an AI ADMIN export imports successfully;
- the latest `platform_sync.runs` row for `admin` is `succeeded` with non-zero
  applied session/turn counts;
- FAE synchronization remains successful and unchanged.

Platform startup/deployment must run pending migrations before switching the
application release. A missing required relation aborts deployment before any
process switch. It must not be converted to a warning or repaired lazily by the
request-serving process.

## 5. Failed-turn presentation

Session detail rendering distinguishes three states:

- successful turn with answer: render the answer;
- explicit failed execution, derived from turn outcome or failed Trace: render
  `本轮执行失败` with the safe public error classification and Trace link;
- genuinely incomplete legacy record with no failure evidence: render
  `未记录 Agent 回答`.

The UI does not synthesize an answer, mutate the stored turn, or reveal provider
payloads, credentials, internal paths, or restricted evidence. Existing access
controls for evidence, feedback, and review remain unchanged.

## 6. Cloud-replica acceptance

No cloud-replica schema change is planned unless verification disproves the
current healthy state. Acceptance uses both local and cloud evidence:

- local `platform_read.turns` contains the successful image-analysis and
  interview turns with non-empty answers;
- cloud import generation advances past the relevant local watermark;
- the HR session `last_active_at`, one-year `expires_at`, and generation match
  the imported state;
- authenticated Session detail displays both successful answers;
- historical failures display `本轮执行失败`, not `未记录 Agent 回答`;
- the five-minute job has no queued batch older than its allowed delivery
  window and its last exit is zero.

## 7. Testing

### MetaBot

- Adapter tests prove the serial tool-choice transformation for absent,
  `auto`, `any`, `tool`, and `none` choices and prove non-tool requests are
  unchanged.
- Recovery policy tests start red for a fresh-session concurrency failure and
  stacked recovery, then prove one fresh replay only.
- MessageBridge tests cover all four execution paths, side effects,
  cancellation, usable output, retry failure, one final delivery, and preserved
  attempt evidence.
- Full MetaBot tests, bridge build, targeted lint, and formatting pass.
- A nine-image production canary shows nine sequential promoted-image requests,
  no parallel group, non-empty answer, and one terminal Feishu message.

### Web research

- Tests prove execution uses the private absolute binary and rejects global
  fallback.
- Version, authentication, retry budget, circuit half-open, timeout, result
  validation, and process-group cleanup tests pass.
- A real HR-scoped query returns public URLs and a fetch returns validated
  content.

### Platform

- Migration applies twice idempotently and grants only intended roles.
- Import integration test fails without migration 011 and succeeds after the
  migration runner.
- UI tests cover success, explicit failure, and genuinely missing legacy
  answer states.
- Backend and Web UI suites pass before deployment.

## 8. Deployment sequence and boundaries

1. Deploy the private web-research toolchain and sidecar. Do not restart
   MetaBot.
2. Deploy only `metabot-hr` from a code/build-only release. Do not move the
   shared MetaBot `current` pointer used by other bots.
3. Acquire the Platform publication lock, apply the migration, and deploy the
   Platform release. Do not change its Nginx server block or `/office/` route.
4. Run end-to-end canaries and cloud-replica acceptance.

Production Platform staging must be
`/data/staging/ai-agent-platform/<deployment_id>/` and cleaned by an exact-path
trap on success or failure. Releases contain code/build artifacts only. Root
keeps current plus two rollback releases; older releases move to
`/data/archive/ai-agent-platform/releases/` and retain at most ten or thirty
days, whichever is stricter. Persistent databases, uploads, logs, indexes,
backups, virtual environments, `node_modules`, and model caches never enter a
release.

Before Platform publication run `df -B1 / /data`. Abort below 25 GB root free,
when projected work would leave below 20 GB, or when projected post-release
root utilization exceeds 75%. Any net growth over 1 GB requires an itemized
explanation. Docker cleanup is limited to unreferenced Platform images older
than current plus two rollback images; never run an unverified
`docker system prune -a`.

## 9. Rollback

- Web research: repoint only its service release and private toolchain to the
  preceding accepted versions, then rerun health/search/fetch probes.
- MetaBot: restore only `metabot-hr` to release `095edf627bc1336e36de94674bdf42ede529318f`.
- Platform: restore the previous application/image release. Migration 011 is an
  additive table and is not dropped during application rollback; dropping it
  would destroy trusted subject-link state.
- Do not delete or rewrite failed Turns, Traces, Claude transcripts, or sync-run
  evidence during rollback.

## 10. Completion evidence

The final report must include:

- focused and full test/build results for all changed repositories;
- active commits/releases and two retained rollback versions;
- before/after disk usage and itemized release sizes;
- exact staging cleanup result;
- current and rollback Docker images for Platform;
- `metabot-hr`, web-research, Platform, FAE, and unaffected-bot health/isolation;
- serial nine-image canary, HR search/fetch canary, Flywheel non-empty answers,
  cloud generation, and authenticated Session-page result;
- restored successful AI ADMIN sync and unchanged FAE sync;
- explicit statement of whether shared Nginx or another application was
  modified.
