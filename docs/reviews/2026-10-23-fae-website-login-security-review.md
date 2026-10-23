# Independent website SNS login security/composition review

Reviewer: Codex independent security/spec/composition reviewer, 2026-10-23.
Reviewed exact HEAD `619f54a023feac462998a6b20c9a3d15737073d3`, branch `feat/fae-external-dingtalk-20261023`, clean working tree. Task baseline `1c5e95e7809ae1d666814620c8066872d2cef002`; final package baseline `d15c17e7dbdf1c59444b201a479016bb6cec19c6` (eight commits).

Scope: AGENTS.md, task brief, implementer report, latest design, and all nonduplicated hunks in the final package (which includes the task delta). Additional reads were limited to the concrete composition risks of broker state consumption, request-log redaction, and ingress/access-log configuration, plus retained official primary bodies. No code edits, checkout/merge, deployment, production request, browser action, agent delegation or test-suite rerun. The only written artifact is this report.

## Findings

- **Critical: none found.**
- **Important implementation/security: none found.**
- **Important acceptance limitation, not an optional-trial blocker:** eligibility of the selected registration remains unproved. The retained official website tutorial explicitly describes site-handled external users and the requested QR/code flow, but its historical notice specifically assures previously using applications; the SNS API permissions table lists internal and third-party enterprise applications as unsupported and personal applications as supported. This conflict precludes a normal-support claim for the selected existing enterprise AppKey. One deliberate invalid-code response (HTTP 200 / 40078) establishes neither valid signature/key acceptance nor outsider eligibility. An actual authorized outsider scan, successful signed callback, verified site UUID and restricted FAE session are still missing. The previous modern-OAuth cross-organization failure remains failed, separately from the new SNS trial's pending result.
- **Minor documentary inconsistency:** `docs/superpowers/specs/2026-10-23-fae-external-dingtalk-design.md:90` still labels the earlier repair “current”, rejects an equal enterprise Client ID at line 93 and says to preserve modern OAuth at line 95; lines 112–116 still describe authCode plus matching code. The opening also describes the old state and anonymous-disabled policy. The latest section at line 119 onward explicitly supersedes these, and the runbook correctly marks historical phases, so there is no operative code contradiction. Mark the earlier design sections historical/superseded or add a clear top-level precedence pointer. Likewise, retain the old registration-isolation plan as historical, not executable current release guidance.

## Task Spec verdict: PASS

- Authorization is exactly HTTPS `oapi.dingtalk.com/connect/qrconnect` with appid, response_type=code, scope=snsapi_login, state and fixed callback.
- One POST to `sns/getuserinfo_bycode` signs the millisecond timestamp with HMAC-SHA256(secret, timestamp), Base64, then HTTPX parameter encoding once. No retries, user-token/profile hop, enterprise identity/directory request, protocol fallback, SMS or email path.
- Proof requires HTTP 200, exact integer errcode zero and validated nested unionid/nick. Malformed/error responses fail closed. Failures log fixed reasons and bounded numeric diagnostic fields; no body/exception URL is emitted. Shared HTTPX/HTTPCore filtering masks signature query values; request context masks raw code, secret and signature. Source deployment uses no Uvicorn access log and a named redacted Nginx format; actual runtime parity remains a controller release check.
- Broker emits/accepts only `fae-dt-sns-v1.`. Both new and legacy prefixes dispatch before a valid corporate cookie; legacy is rejected before state storage/exchange. Route validation requires exactly state/code, rejects duplicates/unknown/error/authCode parameters before consuming state, and preserves nonce/rate-limit boundaries.
- Explicit external Client ID and private secret-file settings are mandatory. Enterprise-only configuration fails; explicitly equal ID is permitted without eligibility inference and uses the explicit external secret setting. Corporate credential fields and factory behavior remain unchanged.
- Existing one-use five-minute attempts, nonce, callback proof, eight-hour lease, local revocation, encrypted registration-scoped identity, random site UUID, FAE-only grant and no Platform session are preserved. SQL schema and FAE consumer were not changed.

## Task Quality verdict: PASS, with the Minor documentation note

Implementation is narrowly scoped and fail-closed. The independent literal HMAC vector and encoding check test the wire contract; the disposable-Postgres flow covers nonce, invalid-input nonconsumption, legacy dispatch with valid corporate cookie, real provisioning/launch exchange, stable UUID reuse, account separation, revoked grant denial and replay. Existing lease/release-gate tests remain in the focused evidence.

The implementer reports **666 passed, 0 failed, 0 skipped, 139 existing cookie deprecation warnings** in the final focused run. The retained final log confirms 666/139 in 27.65s. Runs overlap and are not summed. These are mocked DingTalk/disposable-PG engineering results, not live vendor acceptance. This reviewer did not rerun them.

## Final Readiness verdict

**READY for the already owner-authorized OPTIONAL engineering trial, subject to executing the stated release controls; NOT accepted as normal external login.** No new approval prerequisite is needed.

The explicit candidate `PLATFORM_PARTNER_DINGTALK_CLIENT_ID=dinggtdockkfve0jstjl` and `PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET_FILE=/run/secrets/dingtalk-app-secret` is technically consistent with this implementation. Reusing the same values through explicit settings does not create an implicit fallback or prove application eligibility. Record this exact profile and pending eligibility in the actual rollout record; the runbook's separate-secret path is an example, not a requirement to create or rotate a secret.

Controller's proposed release is defensible: integrate clean master, build the exact merged SHA, verify frontend unchanged, and perform a shared-lock API-only transaction preserving all 32 current Compose inputs. Permit only image, release SHA and the two explicit SNS settings; preserve command, mounts, other settings, containers, Nginx and FAE, with health rollback. The current runtime `81d553...` and source master `d15c17e7...` are distinct baselines, not interchangeable. These are proposed controls supplied by the controller, not operations verified by this read-only review. Confirm runtime query-log protection during parity checks before inviting the scan.

FAE `2636f73` remains anonymous-enabled and active. This release therefore adds an optional external entry trial; it neither restores mandatory login nor proves the original business issue fixed. Normal external-login acceptance remains blocked by the missing real SNS evidence above. A first successful account would prove only that observed account/profile flow; broader account isolation/revocation/replay acceptance must not be claimed from mocks or the single account alone. Keep the existing strict v2 owner authorization pending status truthful.


## Controller verification and selected trial profile

Exact implementation619f54a023feac462998a6b20c9a3d15737073d3: controller independently
ran provider/integration/registration/broker suites,191 passed in4.73s, no failures,
skips or warnings. Full implementer focused suite666 passed with139 inherited Starlette
warnings, including disposable PostgreSQL and mocked provider HTTP; counts overlap.

Trial profile is explicitly configured with existing registered ClientID
`dinggtdockkfve0jstjl` and its existing private secret-file path, not an implicit factory
fallback. No secret value is duplicated or published. The eligibility contradiction in
historical docs remains unresolved; this profile is a candidate for one real external
SNS scan. HTTP200/40078 from the deliberately invalid-code request proves no successful
signature, key or external identity acceptance. Normal external-login acceptance remains
pending. Current owner-authorized v2 release policy permits an optional trial; FAE public
access2636f73 remains enabled, and the trial does not reinstate mandatory login.

Minor current/historical design labels corrected in this documentation commit. The
current explicit profile resolves the earlier missing-config startup dependency for
this trial; superseded modern-candidate off-master guidance is historical. No code,
DB migration or FAE consumer changes were introduced by this review record.
