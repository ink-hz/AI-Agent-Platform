# Owner-authorized external DingTalk rollout

> Implement via subagent-driven-development, test-driven-development, independent review and verification-before-completion.

## Latest user authorization

2026-10-23 user: “我觉得可以直接上线，然后内外部登录扫码的区别是什么”. This supersedes the earlier operator policy of requiring two real external accounts before enabling. Actual real-account acceptance remains pending, never manufactured as passed.

## Architecture and scope

Keep strict v1 real-account release documents intact. Add one explicit closed v2 authorization document supported by both Platform and FAE gates, limited to `dingtalk_external`. Its exact six fields are:

- `contract_version`: `orbbec-fae-partner-provider/v2`
- `provider_kind`: `dingtalk_external`
- `release_basis`: `project_owner_authorized`
- `authorized_at`: RFC3339 timestamp, not future or older than existing180-day bound
- `evidence_sha256`: SHA256 of private archived user-authorization/review record (authorization evidence, not real-account test evidence)
- `real_account_acceptance`: `pending`

Successful gate reason is `partner_release_owner_authorized`, distinct from v1 `partner_release_validated`. For Platform validated object, v2 has dev_real_account_tested_at=None, authorized_at set and explicit release_basis/real_account_acceptance; v1 fields/defaults remain truthful. Both gates retain secure regular-file checks, mode/owner/no-symlink, pinned digest, expiry, exact kind, no reference provider, fixed start URL, production environment. V2 must reject real-account pass fields and any unknown fields. No change to authentication, provider verification, automatic grants, membership, revocation, state/nonce, encryption or access policy.

Alternatives rejected: setting v1 test booleans true would fabricate evidence; removing the gate would silently alter rollout policy. An explicit v2 policy represents current authorization while retaining a separate pending acceptance record.

## Tasks

- [ ] Platform gate + new separate v2 JSON schema + RED/GREEN compatibility/negative tests.
- [ ] FAE gate + RED/GREEN v1/v2 parity and unknown fields, pending state, date, kind, file/digest tests. Public capabilities remains exact v1 bool shape.
- [ ] Independent review of both gate diffs and whole rollout configuration.
- [ ] Production backup and additive123 migration only; new purpose-separated production partner keys; archive honest authorization document and install identical pinned service copies.
- [ ] Disable temporary Dev edge routes before enabling production namespace; preserve enterprise callback, shared Nginx/temp permissions and unrelated locations. Stop only Dev probe after its evidence is retained.
- [ ] Publish reviewed Platform main image/config via image-only scoped Compose transaction preserving latest complete overlays. Gate configuration changes explicitly reviewed; do not reset other applications.
- [ ] Publish FAE through standard full release gates, same provider/digest file. Anonymous remains401; external capability true and /partner/login -> production personalopenid authorization.
- [ ] Verify public script completeness, services, exact releases, no anonymous/invalid nonce bypass. Report external real-person login still pending until observed; no model/eval against production.
