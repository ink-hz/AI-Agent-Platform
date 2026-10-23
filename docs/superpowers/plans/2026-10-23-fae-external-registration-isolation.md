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

- [ ] Write a RED regression proving existing enterprise-only configuration is rejected rather than used by the external factory. Use actual Config via `replace(load_config(), control_plane=...)`, a private test file and a minimal service exposing `external_verified_at`.

```python
with pytest.raises(RuntimeError, match="external_dingtalk_credentials_required"):
    build_external_dingtalk_provider(config_with_only_enterprise_credentials, service)
```

Run the new targeted test and record the expected DID NOT RAISE failure before implementation.

- [ ] Add Config defaults and environment loading:

```python
partner_dingtalk_client_id: str = ""
partner_dingtalk_client_secret_file: str = ""
```

```python
partner_dingtalk_client_id=os.getenv("PLATFORM_PARTNER_DINGTALK_CLIENT_ID", "").strip(),
partner_dingtalk_client_secret_file=os.getenv("PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET_FILE", "").strip(),
```

Add `PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET` to `_INLINE_CONTROL_SECRET_ENV`.

- [ ] Change the external factory only:

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

- [ ] Cover positive external Client ID selection in the real authorization URL; missing one/both settings; matching enterprise ID; unreadable, group-readable, symlink and relative secret files; inline secret rejection; unrelated enterprise settings unchanged. Test config environment loading/defaults as well. Update the existing full app factory integration to supply distinct external settings through the environment.
- [ ] Update runbook opening/prepared config to remove credential reuse guidance. Record confirmed production failure, official references, separate credential names, matching-protocol dependency and why this candidate is not deployed without eligible credentials. Keep historical release evidence intact and clearly dated.
- [ ] Run covering tests with `PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest backend/tests/test_external_dingtalk_registration_config.py backend/tests/test_external_dingtalk_integration.py backend/tests/test_dingtalk_external_provider.py backend/tests/test_config.py backend/tests/test_dingtalk_auth_api.py -q` (adjust exact provider-test filename from `rg --files`). Run real disposable PostgreSQL tests in that group when available; no production calls.
- [ ] Self-review, commit the scoped code/test/runbook changes and report RED/GREEN evidence plus real-login/deployment limitation. Controller requests independent task and final review, merges clean master, and preserves the candidate as undeployed until matching eligible credentials exist.
