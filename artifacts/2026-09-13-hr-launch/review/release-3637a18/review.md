# Independent review — root release policy and cloud resume

Reviewed exact commit `3637a18baef3b54703d787376e8e56824fb54f04`, parent `9c85cf416edd7b83446c52071175f3cefba72468`, exactly ten changed files. Review target was `git show`/commit objects, not the moving checkout. Product files were not edited and tests were not rerun. The reviewer authored the parent's readiness work, but did not author these ten root changes.

**Result: no critical or important defect found in this delta that blocks the stated public-only launch scope. No minor code finding requiring correction. This is code/evidence review, not production or full-candidate certification.**

## Compliance and evidence

- `105_hr_cloud_resume.sql:5-48`: normalized function comparison against migration102 shows only `CREATE OR REPLACE` and the added `draining_cloud -> cloud` edge. See `102-to-105-function.diff`. The advisory transaction lock, singleton row lock, exact request replay receipt fields, mismatch rejection, existing phase edges, zero legacy occupancy gate for cloud, zero cloud occupancy gate for legacy, epoch update and receipt insertion are unchanged. No executor startup, ownership transfer, state reset or application privilege grant was added. Replacement retains the existing function identity/ACL; real PG application-role denial exercises that boundary. Migration102/103/104 bytes match the parent exactly.
- `test_hr_cloud_resume.py:37-87`: four new real PG tests exercise active work and lease preservation through resume, admission blocked during draining then reopened only for cloud, concurrent identical request one receipt/one epoch, mismatched replay denial, residual legacy occupancy rejection with unchanged gate/no receipt, and application-role EXECUTE denial. The repository fixture explicitly substitutes scope validation for synthetic public/no-reference work. It is not evidence of real end-user authorization; the state transition itself uses the maintenance DB role, with 2s lock and 3s statement timeouts. These timeout settings are caller-scoped, not newly imposed inside migration105.
- `config.py:52-56` and the existing `check_schema_ready` loop retain all prior cutover digests and add exact105. Preflight adds105 image-byte verification against reviewed constants; the deployment helper requires105 in both root ledgers before HR migration. Existing missing/mismatched104 tests remain and extend to105. Tests for earlier103/104 floor and changed-image diagnostics remain intact. No assertion was removed or weakened.
- `config.py:263-283`: optional policy is an absolute non-symlink regular file with exactly0600 permissions, parsed as an object with exactly five fields. Version/type, public-only scope, nonempty bounded authorization reference, canonical digest of the three effective loaded profiles, and explicit conservative-estimate accounting acknowledgment are required. The validated policy joins configuration revision identity. Provider/model overrides affect the effective profile and therefore its bound digest. Absent policy preserves prior settings construction compatibility.
- `preflight.py:415-443`: the D7 flag requires validated policy on both matching runtime configurations. It does not take caller booleans as approval. `full-candidate` still unconditionally receives `personal_processing_authorizer_absent`; no source in this delta installs or relaxes the personal authorizer. Full-candidate remains false in the positive policy test. API overlay policy path now matches the parent's Worker configuration.
- Seven policy cases cover positive public-only approval, continued missing DB/full-candidate blockers, each of provider/budget/diagnostic drift,0644 permissions, scope escalation and empty reference. Exact-key/version validation is visible in code but not separately claimed as tested cases.

## Recorded results and provenance limits

The command/log hashes and actual argv/exit codes are recorded in `fingerprints.json`; each recorded source.patch hash matches its command receipt. The actual log summaries are:

- cloud-resume RED:3 failed/1 passed; GREEN:18 passed.
- resume-floor RED:2 failed/53 deselected; GREEN:79 passed.
- resume-deployment GREEN:23 passed.
- release-policy RED:7 failed; GREEN:86 passed.

These are overlapping focused suites, not additive unique coverage. They ran from their recorded earlier HEAD plus contemporaneous patches/new-file snapshots, before this exact commit was created. This review does not relabel them as an exact-final-commit integrated run. Root's final frozen integration and production gate must supply that remaining evidence. No production writes are inferred from local test success.

## Scope boundaries for handoff

The policy is a protected operator-review receipt, not a cryptographic approval service and not runtime content classification. Its public-only label does not establish that arbitrary inline user text is non-personal, nor does it authorize candidate/resume processing. Continue to label acceptance as public/synthetic only and retain the personal-authority blocker. File ownership/mount trust and the truth of the authorization reference belong to the operator release procedure;0600 plus profile digest does not independently authenticate the human approver.

A resume transition is an explicit maintenance action. Nothing here automatically restarts a legacy executor or treats phase alone as a restart instruction. Candidate/privacy approval, actual model quality, browser acceptance and production health remain separately evidenced gates.

## Preserved review material

`review.patch` is the full binary/full-index ten-file delta, SHA-256 `174f642408911d35cf5be880ce6f2cb2712672bd40881e12a6c46d193fabc737`. `sources/` retains exact reviewed blobs. `fingerprints.json` records commit, parent, source Git blob IDs/SHA-256 and inspected evidence hashes. Only this new review directory was written.
