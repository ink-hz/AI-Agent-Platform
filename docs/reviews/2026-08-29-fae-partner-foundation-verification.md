# FAE Partner Identity Foundation verification

Date: 2026-08-31
Scope: local implementation and verification only; no merge, push or production deployment

## Result

The Provider-neutral Partner identity foundation is implemented and locally verified in both repositories.
Production Partner login remains deliberately unavailable: no real Provider has been selected and the Reference
Provider is rejected in production. Public FAE, enterprise FAE, Platform `/admin/*` and AI ADMIN `/office/*` were
not changed or deployed by this work.

Implementation revisions before this report:

- Agent Platform: `f4b9aab917029fc6a8cbdae7fb9cb3ffcd23de60`
- AI FAE Agent: `22757c364272fb73aa2b3cc6a32abae872d6ef31`
- Frozen identity contract revision: `0d51db17321b7e656371bed81060035701c72481`
- Frozen identity contract SHA-256: `c8f2298a46261ec03e60edb9bd8b01dc3da4d792cdfe95d8d826621e966ff225`

The implementation is local on `local/fae-partner-foundation` in each repository. It has not been merged or
pushed.

## Verification evidence

### Full suites

| Repository | Command/result |
|---|---|
| Agent Platform backend | `.venv/bin/python -m pytest -q`: 3612 passed, 2 skipped, 0 failed |
| Agent Platform WebUI | `npm test`: 62 files / 496 tests passed; `npm run build`: passed |
| AI FAE Agent backend | pinned contract environment + `.venv/bin/python -m pytest -q`: 2862 passed, 3 skipped, 0 failed |
| AI FAE Agent WebUI | `npm test`: 26 files / 192 tests passed; `npm run build`: passed |

The FAE backend run exported all three mandatory contract pins. A deliberate run with an invalid commit failed
during collection, proving the gate is fail closed; the final recorded run used the exact revision and digest
above.

The Platform suite initially found that an acceptance request placed the CSRF value in a curl command argument.
The script was corrected to consume the private mode-0600 curl configuration instead. The regression and full
suite then passed. This failure is not omitted from the evidence because it demonstrates the verification gate
found a real process-list exposure before release.

### Independent review and remediation

Independent commit reviews were run against both release gates. The first pass found concrete failures rather
than cosmetic findings: unsafe evidence-file ownership assumptions, collapsed failure reasons, recursive JSON,
an FAE Provider-kind mismatch path, a health signal that could disagree with the mounted login routes, and a
blocking special-file read. Each issue was reproduced with a failing test before correction. The final
implementation now accepts the documented root/service-group evidence mode under the actual FAE container user,
keeps identity enablement as a prerequisite in every environment, and opens evidence fail closed without
blocking startup. Final independent reviews of Platform `f4b9aab9` and FAE `22757c36` reported no actionable
defects.

### Migration and contract upgrade evidence

- Platform migrations `053` through `057` are additive and were exercised by the full migration suite, including
  legacy upgrade paths, least-privilege roles and idempotent application.
- FAE migrations `009` and `010` were exercised against temporary PostgreSQL, including rolling compatibility,
  authenticated Session migration and encrypted conversation restoration.
- The FAE contract consumer rejected an absent or unknown Platform revision. It accepts a supplied known revision
  only when that revision's contract subtree is byte-identical to the checked-out subtree and matches the pinned
  SHA-256; the recorded release run supplied the frozen revision above. Closed schemas reject additional fields.
- Release evidence is either a service-owned regular file no broader than `0600`, or a root-owned
  service-group-readable copy no broader than `0640`. It is opened once with `O_NOFOLLOW | O_NONBLOCK`, bounded
  to 4096 bytes and pinned by SHA-256; special files and recursive JSON fail closed without blocking startup.
- FAE requires its explicitly configured Provider kind to match the release document, while Platform additionally
  verifies that the same kind is registered for production. FAE health exposes the sanitized gate reason without
  exposing the release path or digest. A validated Partner release cannot report login available unless the FAE
  Platform identity/session bottom is also enabled. Unknown runtime environments keep the Partner entry unavailable.

### Two-subject Reference Provider Dev scenario

The deterministic Dev harness used two distinct reference subjects and repeated operator A authentication to
prove stability. Only internal test subject UUIDs are recorded here:

- operator A subject: `4fd1686f-3e89-4e32-9fe9-932c41e4274c`
- operator B subject: `810d5b92-4c72-4c6b-b61b-1e107fb77b10`
- enterprise comparison subject: `b78b3205-2c86-424a-a217-775382c208bd`
- run time: `2026-08-31T20:56:54+08:00`
- capability/identity contract hash: `c8f2298a46261ec03e60edb9bd8b01dc3da4d792cdfe95d8d826621e966ff225`

The scenario was executed as two coordinated, deterministic repository harnesses rather than a live external
Provider login. Platform: 10 focused checks passed. FAE: 12 focused checks passed. Together they proved:

| Scenario step | Observed result |
|---|---|
| Organization/operators created | active organization and two distinct stable subjects; operators receive no implicit FAE grant |
| First unknown login | explicit `partner_binding_required`, HTTP 403, no Platform Session or launch code |
| Owner link and FAE grant | only the linked, active, explicitly granted subject becomes launchable |
| Launch and exchange | 60-second agent-bound code contains only generic subject identity; exchange remains single-use |
| A conversation/history continuity | own create/read/list/continue paths succeed, including a fresh app process backed by PostgreSQL |
| B probes A conversation | 404 before decryption; no content is returned |
| A attachment and Feedback | upload succeeds (201), own bind/read succeeds, own Feedback succeeds (200) |
| B probes A attachment/Feedback | attachment read/delete/bind is 403; foreign-turn Feedback is 404 and is not written |
| A second browser | a new authenticated browser Session lists and continues A's persisted conversation |
| A suspension/provider revocation | access and Binding validation fail closed; no stale allow-cache survives the 60-second bound |
| B remains active | B's independent subject and own conversation remain usable |
| Enterprise/public parity | enterprise and Partner snapshots use the same model, Loop, tools, knowledge, attachment and vision limits; anonymous public chat remains separately available |

No token, raw Provider subject, callback code, question, answer or attachment name is recorded in this report.
Real two-operator acceptance is intentionally deferred until a real Provider is selected.

## Route and rollback evidence

`deploy/cloud/accept.sh` and its policy tests require the following exact release evidence:

```text
PARTNER_PROVIDER_CONFIG_VALID=true
PARTNER_LOGIN_EXPECTED=false
PUBLIC_FAE_CHAT_UNCHANGED=true
ENTERPRISE_FAE_LAUNCH_UNCHANGED=true
OFFICE_ROUTE_UNCHANGED=true
PLATFORM_ADMIN_ROUTE_UNCHANGED=true
```

The default cloud compose configuration keeps Partner identity disabled and all Provider release values empty.
The FAE capability is exactly `{"partner_login_available":false}` and the UI renders no Partner control or
"preparing" placeholder. The rollback design disables only Partner start/callback routing and revokes bindings;
it preserves rows and audit evidence and does not restart unrelated Agents.

No live rollback was executed because there was no production deployment in this task. Local acceptance policy,
startup-gate, route, migration and rollback-structure tests passed; production pre/post snapshots and the actual
rollback transaction remain mandatory in a later authorized deployment.

## Deferred items

- Select and probe the real Partner Provider with two real operator accounts.
- Produce Provider-specific credentials, callback, revocation and token-lifetime design without shared passwords.
- Run the production acceptance transaction and rollback exercise with Partner login still disabled before any
  Provider-specific enablement.

The following non-blocking engineering follow-ups are tracked here so they remain available in a fresh clone;
none weakens the production login gate established by this release:

- Add and verify a lookup-HMAC maintenance rotation workflow before any key-policy rollover; version removal
  continues to fail closed until linked mappings and incompatible pending requests are resolved.
- Keep keyring-size and clarification-round limits synchronized with their loaders, and add symmetric boundary
  tests if release policy requires those caps to be immutable.
- Consider a durable provenance marker for anonymous Feedback accepted while target resolution is unavailable,
  plus aggregation or a lower log level for the steady-state database-less public Feedback warning.
- Harden authenticated history edge cases: nullable legacy titles, empty Session IDs, empty-answer presentation,
  repeated account projection reads, and attachment additions racing an older history-detail response.
- Every full FAE run that collects the frozen contract tests must supply the absolute Platform repository root, a
  known committed Platform revision, and the exact contract-subtree SHA-256; collection remains fail closed.
