# Security second batch implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement one scoped task at a time with independent task review and a final whole-branch review. Steps use checkbox tracking.

**Goal:** Preserve deployed ADMIN subject-binding security on master and enforce least-privilege DAQ execution without changing its Agent contracts or current data.

**Status:** DAQ master bd875ca; reviewed recipe9439b21 → candidate7360; helpercfc1510e. All11 applicable native gates have PASS evidence (8 latest +3 unchanged inherited). Production is BLOCKED_PRESTOP / execution_completion_unproven; no source stop, observer or new controller lease was created. Cutover/V12 remain pending. All failures retained. Platform base advanced01368dea→1934533e to preserve completed cloud-sync work.

**Architecture:** Reuse the approved platform security design B batch. Port the exact already-deployed ADMIN fix instead of redeploying an old application snapshot. For DAQ, preserve exact production application/dependency/knowledge bytes and original base/root-rollback image5ed; run the reviewed offline metadata-only candidate7360 with a separately versioned non-root configuration, read-only assets, same-byte strict-owner secrets and explicit attachment/trace mounts. Controller owns lifecycle, isolation, quiescence and data-preserving rollback.

**Tech Stack:** Python3, pytest, Docker/Compose, existing PostgreSQL, current DAQ authentication/key/knowledge loaders.

## Global Constraints

- User authorized continued iteration under the existing cross-repository security design. FAE application repair remains acceptance-only; no SSH/firewall/account change before independent rescue information is verified.
- Never execute production nginx -t -c or nginx -T -c. Do not modify shared Nginx or other services.
- Preserve source master, user worktrees/materials, exact running IDs/image/config identities and signed code/knowledge bindings. No upstream pin, model/prompt/Loop, DB/network/role migration or business fallback changes.
- No model/business messages, production synthetic conversation/attachment writes or browser automation. Engineering provider/identity substitutions must be identified; full business quality/SSO/browser/DR remain separate.
- No secret values in CLI/logs/reports/image build contexts. Original DAQ source secrets and data permissions remain unchanged. New private assets are independently owned copies with exact-byte validation, no symlink/hardlink/special-file escapes.
- New DAQ runtime UID/GID21081:21081, rootfs read-only, cap_drop ALL, no-new-privileges:true, no supplementary groups, no Docker socket. The original/base and root-rollback image remains sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7. First native failure proved 20 private code directories prevent non-root imports; spec section12 supersedes the earlier same-image-only choice. Candidate must be an explicitly reviewed offline metadata-only derivative of that exact local base, with unchanged application/dependency/prompt/knowledge bytes and a separately recorded digest. Runtime configuration still has no build/pull operation.
- Session/content secret files owner21081 and mode0600; entitlements and approval public key root:21081 mode0640; secret directory root:21081 mode0750 and mounted RO. Do not relax existing key loaders.
- Keep /app/data/attachments and /app/data/daq_traces.jsonl paths; no whole /app/data mount. Published main/SDK knowledge roots stay byte-identical and RO at their existing internal paths. /tmp is private bounded tmpfs; test initial256MiB and measure, do not silently increase.
- Candidate mounts only isolated attachment/trace/synthetic secrets and isolated PG; startup cleans partial files and expired attachments. Never mount production mutable data just to check startup.
- Stop each real validation batch at first unexplained failure; retain receipts. Any deployment/source drift, unknown lifecycle or cleanup blocks the next dependent operation. No retry-to-pass.

## Task 1: Preserve ADMIN deployed owner fix on master

Requirements and full original code are fixed by commit048d0c89a92fae1ec56623b134bedb883f8549a9. Basecaaeb474d30779f43a8e0b85ee6db76abd437cd8. Files are the six affected Python producers/consumers, migration031, original four tests, one new real-PG HTTP/Redis integration and concise CURRENT_WORK. Server receives only the original two-line identity propagation rather than unrelated branch features.

- [x] Confirm production code byte identities and correct stale-working-tree assumptions.
- [x] Reuse original security tests to observe two separate RED failures on oldmaster.
- [x] Port exact reviewed fix; validate session/Redis/PG/orchestrator/feedback chain,001..029->031/replay and same/cross-user behavior. Restricted real PG; provider/identity and Redis transport substitutions explicitly identified.
- [x] Independent spec/code review, fix future migration fixture range, rerun only the two changed integration cases.
- [x] Fast-forward ADMIN master and push exacte42f529 code,140e6cc integration record.115distinct cases pass; two original unrelated baseline failures retained. No ADMIN redeploy.

## Task 2: DAQ non-root assets and repeatable runtime configuration

Read the complete spec docs/superpowers/specs/2026-10-29-daq-nonroot-design.md (source line evidence,12 acceptance gates and rollback responsibilities). DAQ repo source masterb763cca50d57c680dc7f1e1c56afe845dc6d298b; its app/src/deploy bytes equal actual2caa860 source before this task. Implement in an independent DAQ worktree, preserve master ahead11 existing history; remote must be freshly compared before any integration.

**Files:** deploy/compose.prod.yml; deployment-only runtime asset preparation/verification CLI and tests; concise deployment/security documentation. Changes to application code, key loader or models are excluded. Dockerfile may set a safe numeric default for future builds only if tested; actual candidate is the approved metadata-only derivative of exactbase; see Task2c, which supersedes the initial same-image choice.

**Interfaces:** Input is explicit existing main+SDK roots, original four secret filenames, source attachments/trace, an exclusive new asset directory, UID/GID, and expected manifest identities. Output is root-private metadata/hash manifest plus individually prepared RO assets and RW data, and a sanitized runtime/rollback configuration pair. CLI receives paths/identities, never secret contents. All new target paths are exclusive; originals are never modified. Asset preparation and runtime-data capture must be separate so live RO staging cannot falsely claim a stopped-source capture.

- [x] Write tests before implementation for symlink/special/hardlink refusal, target collision, source mutation/hash drift, strict owner/mode key copies, exact selected knowledge scope, nonempty attachment/trace copy continuity and sanitized reports. Run to RED and preserve output.
- [x] Implement bounded descriptor-based regular-file copy with no-follow/fstat fences, private exclusive target+fsync/manifest publication and strict validation. Copy only two selected knowledge roots and four actual referenced files; preserve signed JSON bytes, key versions, trace history and attachment IDs/content. Do not recursively chown old/shared trees.
- [x] Define the runtime service contract below in canonical Compose and its configuration validator. Require explicitly prepared host paths; missing preparation fails closed rather than falling back to old root/dataRW deployment.

```yaml
user: "21081:21081"
read_only: true
cap_drop: [ALL]
security_opt: ["no-new-privileges:true"]
environment:
  PYTHONDONTWRITEBYTECODE: "1"
  TMPDIR: /tmp
tmpfs:
  - /tmp:rw,noexec,nosuid,nodev,size=256m,uid=21081,gid=21081,mode=0700
```

Keep the existing loopback8081, both networks and fixed172.31.0.9, restart and health contracts. Volume leaves: exact mainrootRO, SDKrootRO, secretsRO, attachmentsRW, tracefileRW. Validate deployment labels/configfile identities without outputting expanded env secrets.

- [x] Provide an isolated native probe/harness reusing existing real authentication/key/parser/storage code and production-like authenticated mode. Only external identity/provider boundaries may be synthetic; actual HTTP, filesystem, crypto and isolated PG must remain real. Probe has no production targets by default and reports every unexecuted gate honestly. Do not manufacture a pass by disabling signature verification, OCR, trace, auth or persistence.
- [x] Run affected local engineering tests and existing relevant deployment contracts. Validate original source/app/prompt/upstream bytes remain unchanged; commit source policy/tests/docs and report exact BASE/HEAD plus targeted evidence. No production mutation or master push by implementer.
- [x] Independent spec+quality review, then parent-controlled native container checks V01..V11 with exactimage, isolated data/PG and bounded resource measurement. Preserve failures and actual owned-container cleanup. Missing passed gates stop cutover; they do not stop preparation of a reviewable result.

## Task 3: Controller-owned DAQ rollout and record

Depends onTask2 review and V01..V11. This is not delegated production mutation. Record every generated config/selector and accurate actual image/code/knowledge separately.

- [ ] Fresh read-only fence actualsourcecff5d7f4…/image5ed10ead…, UID collision, networks/mount overlaps, four source file identities, main/SDKactive identities and trace consumers/rotation. No unknown resource may be inferred safe.
- [ ] Capture original root-private Compose/env identities, exact candidate7360 runtime and original5ed root rollback config, durable intent and exclusive lock. Runtime and rollback both use current authoritative new attachments/trace; image, strict-owner secret source and privilege policy are phase-specific. Preserve the original source model for phaseA; no stale wholedata bind after candidate may-start.
- [ ] First establish trusted DAQ execution-completion evidence with fresh request/upload/lease/process/socket/FD/DB observations. Missing proof is execution_completion_unproven: ABORTED_PRESTOP, keep original source running, no source stop. Only after all prerequisites pass issue one exact-source graceful stop; require terminal acknowledgement and post-stop no-writer/cold-state proof. Unknown writer or mutation remains blocked, with no automatic restart or second instance. Never delete leases or fake success; restore only under the explicit A/B conditions below.
- [ ] Final cold copy+content check to an untouched new runtime data target; source permissions unchanged. Controlled exact candidate7360 service start, actual UID/caps/nnp/mounts, health, signed knowledge/static identity and no permissions errors. Keep other services unchanged. V12 read-only production evidence; absent live trace/employee/attachment coverage stays unvalidated.
- [ ] Restore only after all relevant daemon intents are closed and no unknown writer remains. A applies only before candidate may-start: resume exact original source or recreate its preserved model after controlled removal is confirmed. B applies from any candidate start intent, including unknown results: first resolve lifecycle and stop/verify writers, then original root image5ed with CURRENTNEW attachments/trace and original secrets. No PG rollback or historical overwrite. Unknown lifecycle stays blocked; root recovery is un-hardened and another forward move needs a fresh cold ownership migration.
- [ ] Final independent whole-branch/operational review, update actual pass/fail/unexecuted/version receipts, merge/push platform and DAQ verified changes, preserve private evidence and user materials. No new SSH changes without rescue facts.

## Task 2c: Correct immutable code permissions at the packaging boundary

Inserted before any new native batch after b40a6e8 failed V01. Original batch remains 0 PASS / 1 FAIL / 10 NOT_EXECUTED, production unchanged. Design/requirements are in spec section12. The previous mirror-only runtime choice cannot satisfy non-root access to 20 root0700 code directories.

- [x] First repair/independently review the exact Docker absence protocol and private missing-module diagnostic; explicitly reconcile the original workroot without deleting lease or rewriting its FAIL report.
- [x] TDD a bounded, no-follow build-time normalizer for six immutable roots only; prove before/after content/member identity, safe owners/types and negative refusal before mutation. Add an exact-base derivative Dockerfile and enforce the same permission contract in future full packaging. Build context includes only reviewed recipe/script, no env/key/business data.
- [x] Independent code/design review, integrate recipe on master, then controller builds offline from the exact existing base without package installation or registry pull. Retain base/layer/config/content evidence and actual derived digest; no application-container change.
- [x] Express original/base rollback and derived candidate as separate explicit pinned identities in asset helper, canonical Compose and native/controller. Preserve all file-copy primitives, strict keys, signatures, five runtime leaves and current-data rollback.
- [x] Independently review the integrated change and validate all11 applicable gates with original fixtures/standards. Initial full derived batches retained their first failures; the final affected/remaining batch passed8, with V03–V05 inherited only after unchanged host/image/helper/context proof. Native acceptance does not replace production completion/quiescence gates.

## Task 4: Readiness checkpoint before production mutation

后续用户明确授权的10-30发布见本文件末尾Task5；本节保持10-29实际checkpoint，不回填旧日期已上线。

- [x] Review existing real read-only evidence and record the missing trusted execution-completion interface. A new private controller has21 local engineering passes but remains unreviewed/unpublished; do not execute its host stages to rediscover an already established missing prerequisite.
- [x] Parent records BLOCKED_PRESTOP directly from actual source identity/material/SQL-baseline evidence. Final paired35-container observation passes the explicit mount comparison contract. No source stop, daemon mutation, observer or new lease was created; no controller abort/release receipt is invented.
- [x] Independent final checkpoint documentation review Approved,0C/I/M; preserve private evidence. Mainline integration is recorded separately by the actual Git commit/receipt. Source cutover, cold capture and V12 remain pending until trusted completion and all other real prerequisites are met. Any future controller requires separate full review.

## Task 5: 2026-10-30 authorized quiet-maintenance release

- [x] Preserve fresh mainlines and other sessions; independently review exact source-specific quiet evidence under design section16, keeping the general disconnect/drain gap separate.
- [x] Review bounded single-shot action runner and v4 operator; collect actual Linux proofs, hold the existing global deployment flock and preserve original models/env identities.
- [x] Fresh quiet gate, single graceful source stop, normal physical exit/cgroup/writer/DB-trace proof, cold capture/render/verify, exact stopped rm, approved candidate create and final verify/start. Four terminal ACK0, no force/PG restore or other-service mutation.
- [x] Preserve first static-validator failure; independently review the route/representation correction, perform one read-only reconciliation batch with complete raw/local/public bytes, auth/security/log/fleet/end-lifetime gates. V12 PASS in the declared read-only scope.
- [x] Persist active-binding/final/closeout receipts, release the existing flock and retain current assets plus original data/secrets. SSO/browser/natural trace/general drain/DB-01 remain separate.
- [x] Final operational documentation review C0/I0/M0, private evidence archive verified and documentation integrated through the reviewed mainline closeout commits. Exact integration/push hashes are retained in the private integration receipt. See [actual release record](../../operations/2026-10-30-daq-security-release.md); this checkbox is documentation closeout, not expanded business acceptance.
