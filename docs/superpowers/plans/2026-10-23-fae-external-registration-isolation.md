# FAE external registration isolation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Prevent external DingTalk login from accidentally using the internal enterprise registration.

**Architecture:** Separate external credential configuration at the Platform Provider factory. Keep the modern OAuth adapter and enterprise authentication unchanged; real cross-organization support depends on an eligible registration and matching protocol, and is not established by this preparation.

**Tech Stack:** Python dataclasses, FastAPI, pytest, existing private secret reader.

## Global Constraints

- No fallback to enterprise credentials or another OAuth protocol.
- External Client ID must differ from the configured enterprise AppKey.
- AppSecret must use an absolute private service-owned secret file, never an inline environment value.
- Do not deploy the unconfigured candidate: the current production API has external Provider enabled and requires working internal login.
- No production evals, browser automation, identity migration or forged acceptance results.
- Real external login has failed; engineering tests cannot be presented as real-user acceptance.

## Task 1: Separate external credentials and verify the factory boundary

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_external_dingtalk_integration.py`
- Create: `backend/tests/test_external_dingtalk_registration_config.py`
- Modify: `docs/runbooks/2026-10-23-fae-external-dingtalk.md`

**Interfaces:**
- Consumes: `Config`, `read_secret_file`, `build_external_dingtalk_provider(config, service)`.
- Produces: optional Config fields `partner_dingtalk_client_id` and `partner_dingtalk_client_secret_file`, loaded from the two matching `PLATFORM_PARTNER_DINGTALK_*` variables; external factory requires both and rejects enterprise Client ID reuse.

- [x] Write a RED regression proving existing enterprise-only configuration is rejected rather than used by the external factory. Use actual Config via `replace(load_config(), control_plane=...)`, a private test file and a minimal service exposing `external_verified_at`.

```python
with pytest.raises(RuntimeError, match="external_dingtalk_credentials_required"):
    build_external_dingtalk_provider(config_with_only_enterprise_credentials, service)
```

Run the new targeted test and record the expected DID NOT RAISE failure before implementation.

- [x] Add Config defaults and environment loading:

```python
partner_dingtalk_client_id: str = ""
partner_dingtalk_client_secret_file: str = ""
```

```python
partner_dingtalk_client_id=os.getenv("PLATFORM_PARTNER_DINGTALK_CLIENT_ID", "").strip(),
partner_dingtalk_client_secret_file=os.getenv("PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET_FILE", "").strip(),
```

Add `PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET` to `_INLINE_CONTROL_SECRET_ENV`.

- [x] Change the external factory only:

```python
if not config.partner_dingtalk_client_id or not config.partner_dingtalk_client_secret_file:
    raise RuntimeError("external_dingtalk_credentials_required")
if config.partner_dingtalk_client_id == config.control_plane.dingtalk_app_key:
    raise RuntimeError("external_dingtalk_registration_must_differ_from_enterprise")
return DingTalkExternalIdentityProvider(
    client_id=config.partner_dingtalk_client_id,
    client_secret=read_secret_file(config.partner_dingtalk_client_secret_file),
    redirect_uri="https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback",
    verified_at_for_subject=service.external_verified_at,
)
```

- [x] Cover positive external Client ID selection in the real authorization URL; missing one/both settings; matching enterprise ID; unreadable, group-readable, symlink and relative secret files; inline secret rejection; unrelated enterprise settings unchanged. Test config environment loading/defaults as well. Update the existing full app factory integration to supply distinct external settings through the environment.
- [x] Update runbook opening/prepared config to remove credential reuse guidance. Record confirmed production failure, official references, separate credential names, matching-protocol dependency and why this candidate is not deployed without eligible credentials. Keep historical release evidence intact and clearly dated.
- [x] Run covering tests with `PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest backend/tests/test_external_dingtalk_registration_config.py backend/tests/test_external_dingtalk_integration.py backend/tests/test_dingtalk_external_provider.py backend/tests/test_config.py backend/tests/test_dingtalk_auth_api.py -q` (adjust exact provider-test filename from `rg --files`). Run real disposable PostgreSQL tests in that group when available; no production calls.
- [x] Self-review, commit the scoped code/test/runbook changes and report RED/GREEN evidence plus real-login/deployment limitation. Controller requests independent task and final review and preserves the candidate on its work-in-progress branch, without production deployment or master integration, until matching eligible credentials exist. Integrating a new startup requirement while production has external Provider enabled but lacks those settings could break another worker’s unrelated release.


## Task 2: Accept documented equal callback aliases in modern OAuth

**Files:**
- Modify: `backend/app/control_plane/dingtalk_external_provider.py`
- Modify: `backend/app/control_plane/routes_partner.py`
- Modify: `backend/tests/test_dingtalk_external_provider.py`
- Modify: `backend/tests/test_external_dingtalk_integration.py`
- Modify: `backend/tests/test_external_dingtalk_registration_config.py` (Task 1 review portability correction)
- Modify: `docs/runbooks/2026-10-23-fae-external-dingtalk.md`

**Interfaces:**
- Consumes: existing modern OAuth callback mapping with mandatory `authCode`.
- Produces: optional `code` alias accepted only if equal to the validated `authCode`; no code-only or SNS adapter behavior.

- [x] RED: add one modern provider test based on its existing successful MockTransport fixture, passing the same valid value under both `authCode` and `code`. Expect normal token/profile flow and one verified subject. The current provider must fail with `partner_auth_invalid` before HTTP.
- [x] RED: parameterize the existing real disposable PG HTTP callback/launch test for an optional matching `code`. The alias-present case currently returns401 instead of302. Preserve the existing original test variant.
- [x] GREEN: change only the `code` guards, validating `authCode` as before:

```python
# Provider _callback_code: after state and authCode are validated.
code = _required_ascii(callback.get("authCode"), maximum=4096)
if "code" in callback and callback["code"] != code:
    raise ValueError
return code
```

The existing `error` guard remains unconditional; duplicate keys and all `_required_ascii` validation remain. The HTTP route keeps rejecting duplicates before state consumption and uses this pair condition rather than rejecting any `code`:

```python
("code" in callback and callback["code"] != callback.get("authCode"))
```

Do not change the requirement for authCode or the existing error handling / state dispatch.

- [x] Remove equal alias from invalid-provider parametrization; keep/add mismatched alias, code-only, duplicate-code, duplicate-authCode, duplicate-state, malformed authCode, and error cases. In the PG integration test send a conflicting pair before the valid callback, assert401/no provider HTTP, then use the same state successfully to prove invalid input did not consume it. Repeat the successful callback and assert replay401.
- [x] Update runbook with the documented equal-alias behavior and matching-contract test evidence. Record this as a distinct confirmed modern callback mismatch, not the cause of the authorization-page error, and not proof of external app eligibility.
- [x] Run `PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest backend/tests/test_dingtalk_external_provider.py backend/tests/test_external_dingtalk_integration.py backend/tests/test_dingtalk_auth_api.py -q`; these include disposable PG and mocked provider HTTP, not real DingTalk authorization. Commit scoped changes, write report and request task review. Candidate remains on the work-in-progress branch until the external application registration/protocol dependency is resolved.

- [x] Resolve Task1 review Minor portability finding: the unreadable-secret test currently relies on chmod000, which uid0 can still read. For that case only, keep a real private file and monkeypatch `app.local_secrets.os.open` to raise `PermissionError` when opening that exact file; delegate other paths to the original open. Run the15 registration tests once. This changes only test portability, not secret reader behavior.


## Engineering verification and unresolved business dependency

Task1 implementation043040d3 and Task2 implementation0bc0f1e1 passed independent
spec/quality reviews and final whole-branch review, with no Critical/Important code
findings. The unreadable-test portability Minor was corrected; inherited Starlette
cookie deprecations remain separate test debt. Controller verified exact code0bc0f1e1:
registration/provider/disposablePG integration120 passed in3.15s, zero warnings,
failures or skips. Other reported runs overlap and are not summed.

These completed checkboxes describe engineering preparation only. External real-user
login remains failed/unresolved; eligible application registration, its matching
protocol, and dedicated credentials are missing. The candidate stays on its isolated
work-in-progress branch, off shared master and production. No production changes or
new approval prerequisite have been introduced by this repair batch.
