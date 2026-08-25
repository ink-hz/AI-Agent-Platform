# DingTalk Multi-App Silent SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the existing AI行政小助理 DingTalk H5 application silently authenticate `/office/` through Agent Platform while preserving one employee directory, one `internal_user_id`, and one Platform session.

**Architecture:** Keep the current Platform DingTalk application as the default login application and load additional trusted in-client applications from a root-only JSON secret. The validated login return path selects a server-side application profile; the browser receives only its public AppKey and sends the resulting single-use code back with the selected application ID. Platform exchanges the code with the registered secret and runs the existing identity resolver and session issuance unchanged.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, httpx, pytest, React, TypeScript, DingTalk JSAPI, Vitest, Docker Compose, Bash.

## Global Constraints

- Agent Platform remains the only employee-directory and `internal_user_id` authority.
- AI ADMIN must not receive DingTalk AppSecrets, authorization codes, `userid`, or `unionid`.
- The existing Platform application remains the default for QR and in-client login.
- Missing `PLATFORM_DINGTALK_IN_CLIENT_APPS_FILE` preserves current single-app behavior.
- The additional secret is mounted only into `platform-api`; directory and stream workers remain unchanged.
- No Nginx, FAE, database-schema, AI ADMIN business-code, or AI ADMIN model changes.
- Mobile and PC DingTalk homepages are exactly `https://agent.orbbec.com.cn/office/`.
- Tests must follow red-green-refactor and each behavior is committed after it passes.

---

### Task 1: Parse the trusted in-client application registry

**Files:**
- Create: `backend/app/control_plane/in_client_apps.py`
- Create: `backend/tests/test_dingtalk_in_client_apps.py`

**Interfaces:**
- Consumes: `validate_return_path(value: str | None, *, route_prefix: str) -> str` from `app.control_plane.auth`.
- Produces: `TrustedInClientApp(app_id: str, app_key: str, app_secret: str, return_paths: tuple[str, ...])` and `load_trusted_in_client_apps(path: str, *, route_prefix: str) -> tuple[TrustedInClientApp, ...]`.

- [ ] **Step 1: Write the failing registry tests**

```python
def test_loads_one_root_only_office_application(tmp_path):
    path = write_registry(tmp_path, {
        "schema_version": 1,
        "apps": [{
            "id": "office", "app_key": "office-key",
            "app_secret": "office-secret", "return_paths": ["/office/"],
        }],
    })
    assert load_trusted_in_client_apps(str(path), route_prefix="/") == (
        TrustedInClientApp("office", "office-key", "office-secret", ("/office/",)),
    )

@pytest.mark.parametrize("payload", [
    {},
    {"schema_version": 2, "apps": []},
    {"schema_version": 1, "apps": [{"id": "platform", "app_key": "x", "app_secret": "y", "return_paths": ["/office/"]}]},
    {"schema_version": 1, "apps": [{"id": "office", "app_key": "x", "app_secret": "y", "return_paths": ["https://evil.test/"]}]},
])
def test_rejects_malformed_or_unsafe_registry(tmp_path, payload):
    path = write_registry(tmp_path, payload)
    with pytest.raises(ValueError, match="trusted DingTalk application registry invalid"):
        load_trusted_in_client_apps(str(path), route_prefix="/")

def test_rejects_duplicate_ids_app_keys_and_return_paths(tmp_path):
    path = write_registry(tmp_path, {
        "schema_version": 1,
        "apps": [
            {"id": "office", "app_key": "shared", "app_secret": "one", "return_paths": ["/office/"]},
            {"id": "office", "app_key": "shared", "app_secret": "two", "return_paths": ["/office/"]},
        ],
    })
    with pytest.raises(ValueError, match="trusted DingTalk application registry invalid"):
        load_trusted_in_client_apps(str(path), route_prefix="/")

def test_repr_never_contains_application_secret():
    value = TrustedInClientApp("office", "public-key", "private-secret", ("/office/",))
    assert "public-key" not in repr(value)
    assert "private-secret" not in repr(value)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_dingtalk_in_client_apps.py -q`

Expected: collection fails because `app.control_plane.in_client_apps` does not exist.

- [ ] **Step 3: Implement the strict parser**

```python
@dataclass(frozen=True, repr=False)
class TrustedInClientApp:
    app_id: str
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    return_paths: tuple[str, ...]

    def __repr__(self) -> str:
        return f"TrustedInClientApp(app_id={self.app_id!r}, app_key=<redacted>, app_secret=<redacted>, return_paths={self.return_paths!r})"

def load_trusted_in_client_apps(path: str, *, route_prefix: str) -> tuple[TrustedInClientApp, ...]:
    # Open a regular file, require schema_version == 1, forbid unknown fields,
    # validate bounded ASCII IDs/keys/secrets and exact local return paths,
    # then reject duplicate IDs, keys, and return paths before returning a tuple.
```

The implementation must read through `read_secret_file(..., max_bytes=65_536)`, which rejects symlinks, non-regular files, foreign ownership, and group/world access. It must never include raw payload values in raised errors. The production source file is root-owned `0600`; the API secret-volume copy is service-owned `0600`.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_dingtalk_in_client_apps.py -q`

Expected: all registry tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/control_plane/in_client_apps.py backend/tests/test_dingtalk_in_client_apps.py
git commit -m "feat(identity): parse trusted DingTalk applications"
```

### Task 2: Add optional Platform configuration for the registry

**Files:**
- Modify: `backend/app/control_plane/models.py`
- Modify: `backend/app/config.py`
- Modify: `backend/tests/test_control_plane_config.py`

**Interfaces:**
- Consumes: the private-file validation already used for DingTalk AppSecret.
- Produces: `ControlPlaneConfig.dingtalk_in_client_apps_file: str = ""`.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_optional_in_client_registry_is_absent_by_default(tmp_path, monkeypatch):
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")
    monkeypatch.delenv("PLATFORM_DINGTALK_IN_CLIENT_APPS_FILE", raising=False)
    assert load_config().control_plane.dingtalk_in_client_apps_file == ""

def test_in_client_registry_must_be_a_private_regular_file(tmp_path, monkeypatch):
    install_required_identity_environment(tmp_path, monkeypatch, mode="production")
    registry = tmp_path / "dingtalk-in-client-apps.json"
    registry.write_text('{"schema_version":1,"apps":[]}', encoding="utf-8")
    registry.chmod(0o600)
    monkeypatch.setenv("PLATFORM_DINGTALK_IN_CLIENT_APPS_FILE", str(registry))
    assert load_config().control_plane.dingtalk_in_client_apps_file == str(registry)
    registry.chmod(0o644)
    with pytest.raises(ValueError, match="trusted DingTalk application registry"):
        load_config()
```

- [ ] **Step 2: Run and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_control_plane_config.py -q`

Expected: tests fail because the configuration field is missing.

- [ ] **Step 3: Implement optional file configuration**

```python
dingtalk_in_client_apps_file: str = ""

registry_file = os.getenv("PLATFORM_DINGTALK_IN_CLIENT_APPS_FILE", "").strip()
if registry_file:
    _validate_private_file(registry_file, "trusted DingTalk application registry")
```

Pass the validated value into `ControlPlaneConfig`; do not add an inline-secret environment alternative.

- [ ] **Step 4: Run and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_control_plane_config.py -q`

Expected: all configuration tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/control_plane/models.py backend/app/config.py backend/tests/test_control_plane_config.py
git commit -m "feat(identity): configure trusted DingTalk registry"
```

### Task 3: Select and exchange through registered in-client applications

**Files:**
- Modify: `backend/app/control_plane/auth.py`
- Modify: `backend/tests/test_web_session_security.py`

**Interfaces:**
- Consumes: `TrustedInClientApp` data after clients are built.
- Produces: `InClientAuthProfile(app_id, app_key, return_paths, login)`; `DingTalkWebAuth.in_client_configuration(return_path) -> tuple[str, str]`; and backward-compatible `DingTalkWebAuth.complete_in_client(code, browser_challenge=None, edge_ip=None, *, app_id="platform")`.

- [ ] **Step 1: Write failing auth-selection tests**

```python
async def office_login(code: str, verifier: str):
    calls.append(("office", code, verifier))
    return user_id

auth = build_auth(in_client_profiles=(
    InClientAuthProfile("office", "office-key", ("/office/",), office_login),
,))

assert auth.in_client_configuration("/office/") == ("office", "office-key")
assert auth.in_client_configuration("/account") == ("platform", "platform-key")
await auth.complete_in_client("office-code", app_id="office")
assert calls[0][0:2] == ("office", "office-code")
with pytest.raises(AuthenticationError):
    await auth.complete_in_client("code", app_id="unknown")
```

Also test duplicate application IDs, duplicate return paths, reserved `platform`, and secret-free repr/error messages.

- [ ] **Step 2: Run and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_web_session_security.py -q`

Expected: tests fail because profiles and application-aware exchange are absent.

- [ ] **Step 3: Implement immutable profile selection**

```python
@dataclass(frozen=True, repr=False)
class InClientAuthProfile:
    app_id: str
    app_key: str = field(repr=False)
    return_paths: tuple[str, ...]
    login: Callable[[str, str], Awaitable[UUID]] = field(repr=False)

def in_client_configuration(self, return_path: str | None) -> tuple[str, str]:
    selected = validate_return_path(return_path, route_prefix=self.route_prefix)
    profile = self._in_client_by_return_path.get(selected, self._platform_in_client)
    return profile.app_id, profile.app_key

async def complete_in_client(
    self,
    code: str,
    browser_challenge: str | None = None,
    edge_ip=None,
    *,
    app_id: str = "platform",
):
    profile = self._in_client_profiles.get(app_id)
    if profile is None:
        raise AuthenticationError("login application invalid")
    # Keep the existing one-time attempt and rate-limit flow, calling profile.login.
```

- [ ] **Step 4: Run and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_web_session_security.py -q`

Expected: all web-session security tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/control_plane/auth.py backend/tests/test_web_session_security.py
git commit -m "feat(identity): select DingTalk login application"
```

### Task 4: Extend the public config and exchange API compatibly

**Files:**
- Modify: `backend/app/control_plane/routes_auth.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`

**Interfaces:**
- Consumes: `auth.in_client_configuration(return_path)` and application-aware `complete_in_client`.
- Produces: `GET /api/v1/auth/dingtalk/config?return_path=...` and `POST /api/v1/auth/dingtalk/in-client/exchange` with optional `app_id`.

- [ ] **Step 1: Write failing API tests**

```python
office = client.get("/api/v1/auth/dingtalk/config?return_path=%2Foffice%2F")
assert office.json() == {
    "client_id": "office-key", "corp_id": "public-corp-id", "app_id": "office",
}
assert office.headers["cache-control"] == "no-store"

platform = client.get("/api/v1/auth/dingtalk/config")
assert platform.json()["app_id"] == "platform"

exchanged = client.post(
    "/api/v1/auth/dingtalk/in-client/exchange",
    json={"code": "one-time-code", "app_id": "office"},
)
assert exchanged.status_code == 200
assert auth.completed_app_id == "office"
```

Add tests for omitted `app_id`, unknown IDs, extra fields, unsafe return paths, and no raw code in errors.

- [ ] **Step 2: Run and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_dingtalk_auth_api.py -q`

Expected: office selection and `app_id` assertions fail.

- [ ] **Step 3: Implement the compatible API contract**

```python
class CodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=2048)
    app_id: str = Field(default="platform", pattern=r"^[a-z][a-z0-9_-]{0,31}$")

@router.get("/api/v1/auth/dingtalk/config")
async def public_dingtalk_config(return_path: str | None = None):
    app_id, app_key = auth.in_client_configuration(return_path)
    return Response(
        content=json.dumps({"client_id": app_key, "corp_id": auth.corp_id, "app_id": app_id}),
        media_type="application/json", headers=_NO_STORE,
    )
```

Map invalid selections to a generic `400 login request invalid`, and pass `payload.app_id` to exchange without logging the payload.

- [ ] **Step 4: Run and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_dingtalk_auth_api.py -q`

Expected: all DingTalk auth API tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/control_plane/routes_auth.py backend/tests/test_dingtalk_auth_api.py
git commit -m "feat(identity): expose application-aware in-client login"
```

### Task 5: Build registered DingTalk clients without duplicating identities

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_main.py`
- Modify: `backend/tests/test_dingtalk_identity.py`

**Interfaces:**
- Consumes: `load_trusted_in_client_apps` and `InClientAuthProfile`.
- Produces: one `DingTalkClient(login_flow="in_client")` and one `IdentityResolver` per registered application, all using the same corporate ID, database, identity codec, and directory snapshot.

- [ ] **Step 1: Write failing builder tests**

```python
def test_control_auth_loads_office_registry_into_in_client_profiles(config_with_registry):
    auth = build_control_plane_auth(config_with_registry)
    assert auth.in_client_configuration("/office/") == ("office", "office-key")
    assert auth.in_client_configuration("/account") == ("platform", "platform-key")

async def test_registered_application_resolves_to_existing_internal_user_id(
    office_resolver, provider_result, expected_internal_user_id
):
    resolved = await office_resolver.resolve_login_identity(
        provider_result, DirectoryFreshness.FRESH
    )
    assert resolved.internal_user_id == expected_internal_user_id
```

Patch network clients with deterministic fakes and assert all close callbacks run.

- [ ] **Step 2: Run and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_main.py backend/tests/test_dingtalk_identity.py -q`

Expected: registry profiles are not built.

- [ ] **Step 3: Implement client construction**

```python
registered = load_trusted_in_client_apps(
    control.dingtalk_in_client_apps_file, route_prefix=control.route_prefix
) if control.dingtalk_in_client_apps_file else ()

for application in registered:
    client = DingTalkClient(
        app_key=application.app_key,
        app_secret=application.app_secret,
        corp_id=control.dingtalk_corp_id,
        login_flow="in_client",
    )
    resolver = IdentityResolver(database_url, corp_id=control.dingtalk_corp_id, client=client, identity_codec=codec)
    profiles.append(InClientAuthProfile(application.app_id, application.app_key, application.return_paths, make_login(client, resolver)))
```

Use a closure factory so each login callable captures its own client and resolver. Add every new client to `close_callbacks`.

- [ ] **Step 4: Run and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_main.py backend/tests/test_dingtalk_identity.py -q`

Expected: all selected builder and identity tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_main.py backend/tests/test_dingtalk_identity.py
git commit -m "feat(identity): build multi-app DingTalk resolvers"
```

### Task 6: Make the login page request the application selected by return path

**Files:**
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/auth.test.ts`
- Modify: `webui/src/pages/LoginPage.tsx`
- Modify: `webui/src/pages/LoginPage.test.tsx`

**Interfaces:**
- Consumes: the new public configuration response and existing validated `LoginReturnPath`.
- Produces: `inClientLogin(returnPath: LoginReturnPath = "/") -> Promise<void>`.

- [ ] **Step 1: Write failing frontend tests**

```typescript
await inClientLogin("/office/");
expect(fetchMock.mock.calls[0][0]).toBe(
  "/api/v1/auth/dingtalk/config?return_path=%2Foffice%2F",
);
expect(requestAuthCode).toHaveBeenCalledWith({ clientId: "office-key", corpId: "corp" });
expect(fetchMock.mock.calls[1][1]?.body).toBe(
  JSON.stringify({ code: "one-time-code", app_id: "office" }),
);

expect(onInClient).toHaveBeenCalledWith("/office/");
```

Also reject missing/extra/malformed `app_id` and prove no code or AppKey is written to storage.

- [ ] **Step 2: Run and verify RED**

Run: `npm test -- --run src/auth.test.ts src/pages/LoginPage.test.tsx`

Expected: functions still use the fixed config URL and code-only exchange.

- [ ] **Step 3: Implement return-path-aware silent login**

```typescript
async function loadPublicDingTalkConfig(returnPath: LoginReturnPath): Promise<{
  client_id: string; corp_id: string; app_id: string;
}> {
  const query = new URLSearchParams({ return_path: returnPath });
  const response = await fetch(platformPath(`/api/v1/auth/dingtalk/config?${query}`), {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  // Parse exactly client_id, corp_id, and app_id.
}

export async function inClientLogin(returnPath: LoginReturnPath = "/"): Promise<void> {
  const config = await loadPublicDingTalkConfig(returnPath);
  const result = await dd.requestAuthCode({ clientId: config.client_id, corpId: config.corp_id });
  await exchangeInClientCode(result.code, config.app_id);
}
```

Update `LoginPage` so its automatic and manual in-client actions receive the same validated `returnPath` already used for QR login.

- [ ] **Step 4: Run and verify GREEN**

Run: `npm test -- --run src/auth.test.ts src/pages/LoginPage.test.tsx`

Expected: selected frontend tests pass.

- [ ] **Step 5: Commit**

```bash
git add webui/src/auth.ts webui/src/auth.test.ts webui/src/pages/LoginPage.tsx webui/src/pages/LoginPage.test.tsx
git commit -m "feat(webui): select DingTalk app for office login"
```

### Task 7: Mount the registry only into the Platform API

**Files:**
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/bootstrap-dingtalk-production-secrets.sh`
- Modify: `backend/tests/test_dingtalk_production_deployment.py`

**Interfaces:**
- Consumes: root-owned `/opt/orbbec-agent-platform/private/dingtalk-in-client-apps.json`.
- Produces: read-only `/run/secrets/dingtalk-in-client-apps.json` in `platform-api` only.

- [ ] **Step 1: Write failing deployment-contract tests**

```python
def test_compose_mounts_multi_app_registry_only_in_platform_api():
    compose = yaml.safe_load((CLOUD / "compose.yaml").read_text())
    assert compose["services"]["platform-api"]["environment"]["PLATFORM_DINGTALK_IN_CLIENT_APPS_FILE"] == "/run/secrets/dingtalk-in-client-apps.json"
    assert "PLATFORM_DINGTALK_IN_CLIENT_APPS_FILE" not in compose["services"]["platform-directory"]["environment"]
    assert "PLATFORM_DINGTALK_IN_CLIENT_APPS_FILE" not in compose["services"]["platform-dingtalk-stream"]["environment"]

def test_secret_bootstrap_requires_and_copies_registry_only_to_api():
    script = (CLOUD / "bootstrap-dingtalk-production-secrets.sh").read_text()
    assert "dingtalk-in-client-apps.json" in script
    api_block, directory_block = script.split("orbbec-agent-platform-directory-secrets", 1)
    assert "cp /source/dingtalk-in-client-apps.json" in api_block
    assert "cp /source/dingtalk-in-client-apps.json" not in directory_block
```

- [ ] **Step 2: Run and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_dingtalk_production_deployment.py -q`

Expected: registry mount/copy assertions fail.

- [ ] **Step 3: Update deployment files**

Add:

```yaml
PLATFORM_DINGTALK_IN_CLIENT_APPS_FILE: /run/secrets/dingtalk-in-client-apps.json
```

Add `dingtalk-in-client-apps.json` to `required_private` and to only the API-volume copy command. Preserve `0600`, UID/GID `10001:10001`, read-only volume mounting, and current directory/stream secret sets.

- [ ] **Step 4: Run and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_dingtalk_production_deployment.py -q`

Expected: all production deployment tests pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/cloud/compose.yaml deploy/cloud/bootstrap-dingtalk-production-secrets.sh backend/tests/test_dingtalk_production_deployment.py
git commit -m "deploy: mount trusted DingTalk application registry"
```

### Task 8: Full verification and release handoff

**Files:**
- Modify: `docs/superpowers/specs/2026-08-25-dingtalk-multi-app-silent-sso-design.md` only if implementation details differ.

**Interfaces:**
- Consumes: all preceding tasks.
- Produces: a tested Platform release candidate and an exact production checklist.

- [ ] **Step 1: Run backend unit and integration tests**

Run: `backend/.venv/bin/pytest backend/tests -q`

Expected: zero failures; environment-dependent tests may retain their established skips.

- [ ] **Step 2: Run frontend tests and build**

Run: `cd webui && npm test -- --run && npm run build`

Expected: all Vitest tests pass and Vite exits 0.

- [ ] **Step 3: Run secret and source scans**

```bash
git diff --check origin/master...HEAD
rg -n "dingrpmujzloo2plhjyp|app_secret|authorization code" backend/app webui/src deploy/cloud
```

Expected: no production AppSecret or authorization code value is committed; the public AppKey may exist only in deployment evidence or documentation when explicitly intended.

- [ ] **Step 4: Verify untouched boundaries**

```bash
git -C ../AI-ADMIN-Agent status --short
git -C ../AI-FAE-Agent status --short
git diff --name-only origin/master...HEAD
```

Expected: this branch changes only Agent Platform files listed above; AI ADMIN and FAE have no task-created changes.

- [ ] **Step 5: Commit any design synchronization**

```bash
git add docs/superpowers/specs/2026-08-25-dingtalk-multi-app-silent-sso-design.md
git commit -m "docs: finalize DingTalk multi-app SSO contract"
```

Skip this commit when the design already matches the implementation exactly.

- [ ] **Step 6: Prepare production actions**

Create the root-owned registry file without echoing either AppSecret, run the existing Platform release workflow, then verify:

```text
GET /api/v1/auth/dingtalk/config                         -> app_id=platform
GET /api/v1/auth/dingtalk/config?return_path=/office/    -> app_id=office
GET /office/ without session in ordinary browser         -> no anonymous data
DingTalk workbench first open                             -> silent identity, /office/
Existing Platform DingTalk login                          -> unchanged
```

Publishing the DingTalk webpage version is the final external action after server verification.
