# FAE Partner Operator Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the provider-neutral Platform identity foundation and FAE subject-owned conversation substrate required for individually authenticated partner customer-service operators, while preserving identical FAE capabilities and leaving production partner login disabled until a real provider passes the approved capability probe.

**Architecture:** Agent Platform owns partner organizations, operators, provider mappings, FAE-only grants, single-use launch codes, bindings, revocation and audit. FAE consumes a generic Platform subject, persists authenticated conversations by `owner_subject_id`, and uses the same model, Loop, knowledge, tools and attachment policies for enterprise and partner subjects. This plan intentionally stops at a production-disabled Reference Provider; after a real provider is selected, its adapter and two-operator pilot receive a separate provider-specific plan.

**Tech Stack:** Python 3.11, FastAPI, psycopg 3, PostgreSQL, Pydantic, React 19, TypeScript, Vitest, pytest, AES-GCM, HMAC-SHA256.

## Global Constraints

- The approved design is `docs/superpowers/specs/2026-08-29-fae-partner-operator-access-design.md`.
- Platform repository root is `/Users/neo/Developer/work/AI-Agent-Platform`.
- FAE repository root is `/Users/neo/Developer/work/AI-FAE-Agent`.
- Use isolated git worktrees at execution time; do not implement in either dirty primary checkout.
- Keep feature branches local, integrate through local worktrees, and do not push any feature branch. Push `master` only when the user explicitly requests it.
- Re-read both repositories' `AGENTS.md`/`CLAUDE.md` instructions before editing.
- Platform control migrations are currently contiguous through `052`; this plan reserves `053`, `054` and `055`. Re-check the highest migration before Task 1 and renumber the whole three-migration sequence if master advanced.
- FAE migrations are currently contiguous through `008`; this plan reserves `009` and `010`. Re-check before Task 6 and renumber both together if master advanced.
- Python commands use Platform `backend/.venv/bin/python` or FAE `.venv/bin/python`; both must report Python 3.11 or newer.
- Partner and enterprise subjects use the same FAE model, Prompt, Agent Loop, capabilities, tools, knowledge sources, attachment limits and error policy.
- `authentication_mode` and `subject_type` must never enter model, tool, knowledge-index, attachment-limit or Loop-budget selection.
- Partner operators do not receive a normal Platform Web Session and cannot access Platform pages, Agent Brain or other Agents.
- No shared partner password, anonymous partner grant, raw provider identifier in FAE, or silent downgrade to `public_customer`.
- A partner Launch Code is opaque, single-use, audience-bound to `ai-fae-agent`, valid for exactly 60 seconds and exchangeable only through the existing private back-channel boundary.
- FAE Binding revalidation cache is at most 60 seconds; Platform unavailability is a visible 503 and never an anonymous fallback.
- Preserve the current FAE cookie name and `orbbec-fae-enterprise` HMAC domain during this release so existing enterprise sessions do not become invalid merely because the Python types become generic.
- `fae.orbbec.com.cn`, `/office/*`, Platform DingTalk login and the public FAE customer path remain unchanged.
- The Reference Provider is allowed only in test/dev. Production config validation must reject it.
- Do not expose a partner login button or “preparing” page while no production Provider release is configured.
- Each task starts with a failing test, ends with focused tests and a commit, and receives review before the next task.

---

## File and Boundary Map

### Agent Platform

- `backend/control_migrations/053_agent_access_subjects.sql`: generic subject projection and enterprise backfill.
- `backend/control_migrations/054_partner_operator_identity.sql`: partner organizations, operators, provider identities, pending binding requests, grants, login attempts and atomic owner mutations.
- `backend/control_migrations/055_generic_agent_launch_bindings.sql`: subject-aware FAE launch codes and bindings while preserving enterprise compatibility.
- `backend/app/control_plane/partner_models.py`: partner domain types and Provider-neutral contracts.
- `backend/app/control_plane/partner_identity_crypto.py`: partner-only provider lookup/encryption boundary with independent key purposes.
- `backend/app/control_plane/partner_repository.py`: database access through execute-only functions.
- `backend/app/control_plane/partner_service.py`: owner management, Provider callback resolution and fail-closed lifecycle decisions.
- `backend/app/control_plane/partner_provider.py`: Provider protocol and dev-only Reference Provider.
- `backend/app/control_plane/routes_partner.py`: owner management routes plus public start/callback routes.
- `backend/app/control_plane/agent_launch.py`: generic launch subject contract and enterprise/partner issuing paths.
- `webui/src/partnerApi.ts`: owner-facing partner API client.
- `webui/src/pages/PartnerAccessPanel.tsx`: owner-only partner organization/operator management UI.
- `contracts/fae_identity_v1/`: cross-repository launch, exchange, validate and capability-parity fixtures.

### FAE

- `migrations/009_fae_authenticated_sessions.sql`: generic authenticated browser sessions with enterprise backfill.
- `migrations/010_fae_authenticated_conversations.sql`: subject ownership columns and encrypted session checkpoints.
- `src/platform_identity/models.py`: generic `PlatformSubject` and authenticated session records.
- `src/platform_identity/service.py`: enterprise/partner session service with unchanged token cryptographic domain.
- `src/platform_identity/client.py`: subject-aware exchange and Binding validation.
- `src/platform_identity/routes.py`: generic launch exchange and authenticated Session boundary.
- `src/storage/authenticated_conversations.py`: PostgreSQL conversation list/load/checkpoint persistence.
- `src/agent/session.py`: subject-owned Session model plus deterministic checkpoint serialization.
- `src/api/routes.py`: subject ownership for chat, history and Feedback.
- `src/api/attachment_routes.py`, `src/attachments/models.py`, `src/attachments/service.py`, `src/attachments/store.py`: generic attachment ownership with legacy manifest read compatibility.
- `webui/src/enterpriseIdentity.ts`: authenticated identity bootstrap extended to `platform_partner` without changing the existing public path.
- `webui/src/AuthenticatedSessionNav.tsx`: paginated authenticated history and cross-device restore.
- `webui/src/App.tsx`: account identity display, partner login link gating and authenticated history integration.

---

### Task 1: Add the generic Platform subject projection

**Files:**
- Create: `backend/control_migrations/053_agent_access_subjects.sql`
- Create: `backend/tests/test_agent_access_subjects_migration.py`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Consumes: existing `platform_control.internal_users(internal_user_id)`.
- Produces: `platform_control.agent_access_subjects`, `platform_control.enterprise_subject_links`, and the invariant `enterprise subject_id = internal_user_id`.

- [ ] **Step 1: Write the failing migration contract tests**

```python
from pathlib import Path

MIGRATION = Path(__file__).parents[1] / "control_migrations/053_agent_access_subjects.sql"


def test_generic_subject_schema_and_enterprise_backfill_are_explicit() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "create type platform_control.agent_subject_type" in sql
    assert "('enterprise_member','partner_operator')" in sql.replace(" ", "")
    assert "create table platform_control.agent_access_subjects" in sql
    assert "create table platform_control.enterprise_subject_links" in sql
    assert "subject_id=users.internal_user_id" in sql.replace(" ", "")
    assert "unique (internal_user_id)" in sql
    assert "revoke all on platform_control.agent_access_subjects from public" in sql


def test_partner_subject_cannot_claim_an_enterprise_internal_user() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "enterprise subject type required" in sql
    assert "partner subject cannot have enterprise link" in sql
```

- [ ] **Step 2: Run the tests and verify the red state**

Run:

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest tests/test_agent_access_subjects_migration.py tests/test_control_plane_migration.py -q
```

Expected: FAIL because migration `053_agent_access_subjects.sql` is absent and the contiguous migration count still ends at 52.

- [ ] **Step 3: Implement migration 053**

Create the enum, subject table, link table, trigger guards and deterministic enterprise backfill. The central SQL must have this shape:

```sql
create type platform_control.agent_subject_type as enum
  ('enterprise_member','partner_operator');

create table platform_control.agent_access_subjects (
  subject_id uuid primary key,
  subject_type platform_control.agent_subject_type not null,
  status text not null check (status in ('active','suspended','disabled')),
  display_name_ciphertext bytea,
  display_name_key_version integer,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  invalidated_at timestamptz,
  check (num_nonnulls(display_name_ciphertext, display_name_key_version) in (0, 2)),
  check (display_name_key_version is null or display_name_key_version > 0)
);

create table platform_control.enterprise_subject_links (
  subject_id uuid primary key
    references platform_control.agent_access_subjects(subject_id) on delete restrict,
  internal_user_id uuid not null unique
    references platform_control.internal_users(internal_user_id) on delete restrict,
  check (subject_id = internal_user_id)
);
```

Backfill one row per `internal_users` row using `subject_id=internal_user_id`. The generic projection deliberately leaves enterprise `display_name_ciphertext` null and continues to resolve enterprise display names through the existing `internal_users`/directory projection; a SQL migration must never copy plaintext into a ciphertext column or invent application key access. Partner creation in Task 2 seals its display name with the existing `ContentCodec`, using AAD subject `agent-subject-display:<subject_id>`, and stores the returned nonce-prefixed ciphertext plus key version. Add a trigger that requires both display-name fields for `partner_operator`, rejects an enterprise link unless the target subject type is `enterprise_member`, and rejects later subject-type mutation while a link exists.

Update the explicit migration-version expectation in `test_control_plane_migration.py` from `range(1, 53)` to `range(1, 54)`.

- [ ] **Step 4: Run focused and real-database migration tests**

Run:

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest tests/test_agent_access_subjects_migration.py tests/test_control_plane_migration.py -q
```

Expected: PASS, including upgrade from migrations 001–052 and a fresh 001–053 database.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/control_migrations/053_agent_access_subjects.sql \
  backend/tests/test_agent_access_subjects_migration.py \
  backend/tests/test_control_plane_migration.py
git commit -m "feat(identity): add generic agent subjects"
```

### Task 2: Add partner organizations, operators and fail-closed owner mutations

**Files:**
- Create: `backend/control_migrations/054_partner_operator_identity.sql`
- Create: `backend/tests/test_partner_operator_migration.py`
- Create: `backend/app/control_plane/partner_models.py`
- Create: `backend/app/control_plane/partner_identity_crypto.py`
- Create: `backend/app/control_plane/partner_repository.py`
- Create: `backend/app/control_plane/partner_service.py`
- Create: `backend/tests/test_partner_service.py`
- Create: `backend/tests/test_partner_identity_crypto.py`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Consumes: `agent_access_subjects` from Task 1 and existing `append_audit_event` discipline.
- Produces: `PartnerService.create_organization`, `create_operator`, `set_organization_status`, `set_operator_status`, `grant_fae`, `revoke_fae`, `record_binding_request`, `link_binding_request`, and `resolve_verified_identity`.

- [ ] **Step 1: Write failing schema and service tests**

```python
def test_partner_scope_requires_all_four_active_layers(service, seeded_partner) -> None:
    decision = service.decide_fae_access(seeded_partner.subject_id)
    assert decision.allowed is True
    service.set_operator_status(
        actor_id=seeded_partner.owner_id,
        operator_id=seeded_partner.operator_id,
        status="suspended",
        reason="contract ended",
        request_id=seeded_partner.request_id,
    )
    assert service.decide_fae_access(seeded_partner.subject_id).reason == "operator_inactive"


def test_partner_mutation_fails_when_audit_append_fails(service, audit) -> None:
    audit.fail = True
    with pytest.raises(PartnerIdentityError, match="required_audit_unavailable"):
        service.create_organization(
            actor_id=OWNER_ID,
            display_name="合作方甲",
            reason="客服试点",
            request_id=REQUEST_ID,
        )
    assert service.list_organizations() == ()
```

The migration test must assert exact tables, encrypted identity columns, HMAC key versions, pending-binding state transitions, owner-only SECURITY DEFINER functions, no table-level application-role writes and FAE-only `agent_id` checks.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest \
  tests/test_partner_operator_migration.py \
  tests/test_partner_identity_crypto.py \
  tests/test_partner_service.py -q
```

Expected: FAIL because partner schema and service types do not exist.

- [ ] **Step 3: Implement the partner domain types**

```python
class PartnerStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


@dataclass(frozen=True)
class PartnerAccessDecision:
    allowed: bool
    reason: str
    subject_id: UUID | None = None


@dataclass(frozen=True)
class VerifiedProviderSubject:
    provider_kind: str
    provider_subject: str = field(repr=False)
    verified_at: datetime
    display_name: str | None = field(default=None, repr=False)


class PartnerIdentityError(RuntimeError):
    def __init__(self, code: str, status_code: int = 503) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)
```

Repository return types expose internal UUIDs and status only. Raw Provider subjects enter only the HMAC/encryption boundary and must be excluded from `repr`.

- [ ] **Step 4: Implement migration 054 and repository/service methods**

Create these tables with `on delete restrict` and status checks:

```text
partner_organizations
partner_operators
partner_provider_identities
partner_identity_binding_requests
partner_agent_grants
partner_login_attempts
```

Use SECURITY DEFINER functions for each owner mutation. Every function must lock the target row, validate `session_user`, validate the actor is the active `platform_owner`, mutate and append its audit event in one transaction. `partner_agent_grants.agent_id` is constrained to `ai-fae-agent`. Provider identity uniqueness is `(provider_kind, provider_subject_lookup_hmac, lookup_key_version)`; raw identity uses versioned ciphertext.

Implement `PartnerProviderIdentityCodec` with two independent `IdentityKeyring` purposes,
`partner-provider-encryption` and `partner-provider-lookup-hmac`. Its normalized HMAC input is
`partner-provider:<provider_kind>:<provider_subject>` and its AES-GCM AAD is
`partner-provider:<provider_kind>:v<encryption_key_version>`. It supports the same ordered HMAC
transition window as the existing enterprise provider codec but does not reuse that codec's
`dingtalk:*` cryptographic domain. Logs and `repr` expose only key versions and stable error codes.

`partner_identity_binding_requests` stores an unknown verified Provider identity as versioned HMAC plus ciphertext, optional encrypted display-name projection, `status in ('pending','linked','rejected','expired')`, 24-hour expiry and a unique active request per Provider lookup value. It never creates a subject, operator, grant, Binding or Launch Code. Linking requires the owner to select an existing operator in the same active organization; the SECURITY DEFINER function atomically creates the Provider identity mapping, marks the request `linked`, appends the audit event and rejects identity conflicts. Rejection/expiry never opens access.

`create_operator` generates the stable `subject_id`, seals the owner-supplied display name before entering
SQL, and atomically inserts the `partner_operator` subject plus operator row and audit event. It does not
grant FAE access; `grant_fae` remains a separate explicit owner mutation.

`PartnerService.decide_fae_access(subject_id)` returns allowed only when subject, organization, operator and grant are all active. It must distinguish `subject_inactive`, `organization_inactive`, `operator_inactive` and `fae_access_denied` without returning PII.

Update the migration count to include 054.

- [ ] **Step 5: Run Task 2 tests**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest \
  tests/test_partner_operator_migration.py \
  tests/test_partner_identity_crypto.py \
  tests/test_partner_service.py \
  tests/test_control_plane_migration.py -q
```

Expected: PASS; the database test also proves audit failure rolls back the mutation.

- [ ] **Step 6: Commit Task 2**

```bash
git add backend/control_migrations/054_partner_operator_identity.sql \
  backend/app/control_plane/partner_models.py \
  backend/app/control_plane/partner_identity_crypto.py \
  backend/app/control_plane/partner_repository.py \
  backend/app/control_plane/partner_service.py \
  backend/tests/test_partner_operator_migration.py \
  backend/tests/test_partner_identity_crypto.py \
  backend/tests/test_partner_service.py \
  backend/tests/test_control_plane_migration.py
git commit -m "feat(identity): manage partner operators"
```

### Task 3: Expose owner-only partner management without granting Platform access

**Files:**
- Create: `backend/app/control_plane/routes_partner.py`
- Create: `backend/tests/test_partner_management_api.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/control_plane/authorization.py`
- Create: `webui/src/partnerApi.ts`
- Create: `webui/src/pages/PartnerAccessPanel.tsx`
- Create: `webui/src/pages/PartnerAccessPanel.test.tsx`
- Modify: `webui/src/pages/IdentityManagementPage.tsx`

**Interfaces:**
- Consumes: `PartnerService` from Task 2 and current `AuthContext` owner authorization.
- Produces: owner-only `/api/v1/manage/partners/*` routes and a management panel embedded in Identity Management.

- [ ] **Step 1: Write failing API authorization tests**

```python
@pytest.mark.parametrize("role", [Role.MEMBER, Role.MANAGEMENT_VIEWER, Role.PLATFORM_ADMIN])
def test_only_platform_owner_can_manage_partners(role, partner_client) -> None:
    response = partner_client(role).post(
        "/api/v1/manage/partners/organizations",
        json={"display_name": "合作方甲", "reason": "客服试点"},
    )
    assert response.status_code == 403


def test_partner_management_response_contains_no_provider_secret(owner_client) -> None:
    response = owner_client.get("/api/v1/manage/partners/operators")
    assert response.status_code == 200
    serialized = response.text.lower()
    assert "provider_subject" not in serialized
    assert "token" not in serialized
    assert "secret" not in serialized
```

- [ ] **Step 2: Write failing UI tests**

```tsx
it("lets only the owner manage partner organizations and operators", async () => {
  render(<PartnerAccessPanel account={owner} />);
  expect(await screen.findByText("合作方客服")).toBeTruthy();
  expect(screen.getByRole("button", { name: "创建合作方" })).toBeTruthy();
  expect(screen.queryByText(/Provider Token/)).toBeNull();
});
```

Also assert that the panel requires a non-empty reason and never renders for `platform_admin`, `management_viewer` or `member`.

- [ ] **Step 3: Run backend and frontend tests to verify failure**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest tests/test_partner_management_api.py -q
cd ../webui
npm test -- PartnerAccessPanel.test.tsx
```

Expected: FAIL because routes, client and panel are absent.

- [ ] **Step 4: Implement exact owner routes**

```text
GET    /api/v1/manage/partners/organizations
POST   /api/v1/manage/partners/organizations
PATCH  /api/v1/manage/partners/organizations/{organization_id}/status
GET    /api/v1/manage/partners/operators
POST   /api/v1/manage/partners/operators
PATCH  /api/v1/manage/partners/operators/{operator_id}/status
PUT    /api/v1/manage/partners/operators/{operator_id}/fae-grant
DELETE /api/v1/manage/partners/operators/{operator_id}/fae-grant
GET    /api/v1/manage/partners/binding-requests
POST   /api/v1/manage/partners/binding-requests/{request_id}/link
POST   /api/v1/manage/partners/binding-requests/{request_id}/reject
```

Mutation models use `extra="forbid"`, strict UUIDs/status enums, `reason` length 3–500 and client-generated `request_id`. Responses contain internal IDs, service-decrypted safe display-name projections, status, grant state and timestamps only; ciphertext is never serialized to the browser.

- [ ] **Step 5: Implement the management panel**

Add one “合作方客服” section below existing enterprise identity controls. It lists organizations, operators and pending identity binding requests; supports create/suspend/disable/reactivate, explicit request-to-operator linking/rejection and FAE grant/revoke; requires a reason; shows an explicit indeterminate state on 5xx; and reuses the existing mutation-integrity pattern instead of optimistic success. The UI never displays the raw Provider subject; each request is represented by internal request ID, Provider kind, safe display-name projection when available, timestamps and status.

Do not create a new top-level navigation tab.

- [ ] **Step 6: Run Task 3 tests and build**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest tests/test_partner_management_api.py tests/test_r1_authorization.py -q
cd ../webui
npm test -- PartnerAccessPanel.test.tsx IdentityManagementPage.test.tsx
npm run build
```

Expected: all tests PASS and the production frontend build succeeds.

- [ ] **Step 7: Commit Task 3**

```bash
git add backend/app/control_plane/routes_partner.py \
  backend/app/main.py backend/app/control_plane/authorization.py \
  backend/tests/test_partner_management_api.py \
  webui/src/partnerApi.ts webui/src/pages/PartnerAccessPanel.tsx \
  webui/src/pages/PartnerAccessPanel.test.tsx \
  webui/src/pages/IdentityManagementPage.tsx
git commit -m "feat(identity): add partner access management"
```

### Task 4: Build the Provider-neutral authentication boundary and dev Reference Provider

**Files:**
- Create: `backend/app/control_plane/partner_provider.py`
- Create: `backend/tests/test_partner_provider.py`
- Modify: `backend/app/control_plane/routes_partner.py`
- Modify: `backend/app/control_plane/partner_service.py`
- Modify: `backend/app/control_plane/middleware.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/app/config.py`
- Modify: `backend/tests/test_partner_management_api.py`

**Interfaces:**
- Consumes: `partner_login_attempts` and `resolve_verified_identity` from Tasks 2–3.
- Produces: `PartnerIdentityProvider.begin_auth`, `finish_auth`, `check_subject`, public `/partner-auth/start` and `/partner-auth/callback`.

- [ ] **Step 1: Write Provider contract and production-rejection tests**

```python
class FakeProvider:
    kind = "fixture"

    def begin_auth(self, state: str) -> str:
        return f"https://provider.invalid/login?state={state}"

    async def finish_auth(self, callback: Mapping[str, str]) -> VerifiedProviderSubject:
        return VerifiedProviderSubject(
            provider_kind=self.kind,
            provider_subject="operator-001",
            display_name="坐席一",
            verified_at=NOW,
        )


def test_reference_provider_is_rejected_in_production(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", "reference")
    with pytest.raises(ValueError, match="partner_reference_provider_forbidden"):
        load_config()
```

Add tests for state expiry, state replay, callback error, exact return path, inactive mapping, Provider unavailable and raw identity absence from response/log records.

- [ ] **Step 2: Run tests and verify failure**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest tests/test_partner_provider.py tests/test_partner_management_api.py -q
```

Expected: FAIL because Provider protocol and auth routes are absent.

- [ ] **Step 3: Implement the protocol and Reference Provider**

```python
class PartnerIdentityProvider(Protocol):
    kind: str

    def begin_auth(self, state: str) -> str:
        raise NotImplementedError

    async def finish_auth(
        self, callback: Mapping[str, str]
    ) -> VerifiedProviderSubject:
        raise NotImplementedError

    async def check_subject(
        self, provider_subject: str
    ) -> Literal["active", "inactive"]:
        raise NotImplementedError


class ReferencePartnerIdentityProvider:
    kind = "reference"

    def __init__(self, identities: Mapping[str, tuple[str, str]]) -> None:
        self._identities = dict(identities)

    def begin_auth(self, state: str) -> str:
        return f"/partner-auth/reference?state={quote(state, safe='')}"

    async def finish_auth(
        self, callback: Mapping[str, str]
    ) -> VerifiedProviderSubject:
        code = callback.get("code", "")
        if code not in self._identities:
            raise PartnerIdentityError("partner_auth_invalid", 401)
        subject, display_name = self._identities[code]
        return VerifiedProviderSubject(
            provider_kind=self.kind,
            provider_subject=subject,
            verified_at=datetime.now(UTC),
            display_name=display_name,
        )

    async def check_subject(
        self, provider_subject: str
    ) -> Literal["active", "inactive"]:
        active = any(
            subject == provider_subject
            for subject, _display_name in self._identities.values()
        )
        return "active" if active else "inactive"
```

Production configuration accepts only a provider release whose adapter is explicitly registered; `reference` is legal only in test/dev.

- [ ] **Step 4: Implement start/callback state handling**

`/partner-auth/start` accepts no arbitrary return URL. It always targets FAE `/app/`, creates a 10-minute state digest in `partner_login_attempts` and redirects to the configured Provider. Callback consumes state exactly once before identity resolution. Unknown Provider identities call `record_binding_request`, return the stable `partner_binding_required` result and never create a subject, grant, Binding or Launch Code. A later login succeeds only after the owner explicitly links that request to an existing operator through Task 3.

Public-route middleware exemptions are exact to `GET /partner-auth/start` and the configured callback method/path. Every other `/partner-auth/*` route is denied by default.

- [ ] **Step 5: Run Task 4 tests**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest \
  tests/test_partner_provider.py \
  tests/test_partner_management_api.py \
  tests/test_r1_authorization.py -q
```

Expected: PASS, including production rejection of Reference Provider.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/app/control_plane/partner_provider.py \
  backend/app/control_plane/routes_partner.py \
  backend/app/control_plane/partner_service.py \
  backend/app/control_plane/middleware.py \
  backend/app/control_plane/authorization.py backend/app/config.py \
  backend/tests/test_partner_provider.py \
  backend/tests/test_partner_management_api.py
git commit -m "feat(identity): add partner provider boundary"
```

### Task 5: Generalize FAE Launch Codes and Bindings to Platform subjects

**Files:**
- Create: `backend/control_migrations/055_generic_agent_launch_bindings.sql`
- Modify: `backend/app/control_plane/agent_launch.py`
- Modify: `backend/app/control_plane/routes_partner.py`
- Modify: `backend/tests/test_agent_launch.py`
- Create: `backend/tests/test_generic_agent_launch_migration.py`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Consumes: enterprise subjects from Task 1 and partner access decisions from Task 2.
- Produces: `ExchangedAgentSubject`, `AgentLaunchService.issue_partner`, generic exchange/validate response v1.

- [ ] **Step 1: Write failing generic launch tests**

```python
def test_partner_launch_contains_only_generic_subject(fake_launch_service) -> None:
    issued = fake_launch_service.issue_partner(PARTNER_SUBJECT_ID)
    exchanged = fake_launch_service.exchange(issued.code)
    assert exchanged.subject_id == PARTNER_SUBJECT_ID
    assert exchanged.subject_type == "partner_operator"
    assert exchanged.internal_user_id is None
    assert exchanged.agent_id == "ai-fae-agent"


def test_enterprise_launch_preserves_internal_user_projection(fake_launch_service) -> None:
    exchanged = fake_launch_service.exchange(
        fake_launch_service.issue_enterprise(ENTERPRISE_CONTEXT).code
    )
    assert exchanged.subject_id == ENTERPRISE_CONTEXT.internal_user_id
    assert exchanged.subject_type == "enterprise_member"
    assert exchanged.internal_user_id == ENTERPRISE_CONTEXT.internal_user_id
```

Add DB tests for partner organization suspension, operator suspension, grant revocation, code replay and Binding validation after revocation.

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest tests/test_agent_launch.py tests/test_generic_agent_launch_migration.py -q
```

Expected: FAIL because v52 stores only `internal_user_id` and requires a Platform Web Session.

- [ ] **Step 3: Implement migration 055**

Add `subject_id` and `subject_type` to launch codes and bindings, backfill enterprise rows with
`subject_id=internal_user_id`, and make `source_session_id/internal_user_id` nullable only for
`partner_operator`. Add checks that enforce these two shapes:

```sql
check (
  (subject_type='enterprise_member' and source_session_id is not null
    and internal_user_id is not null and subject_id=internal_user_id)
  or
  (subject_type='partner_operator' and source_session_id is null
    and internal_user_id is null)
)
```

Create `issue_agent_launch_v55`, `exchange_agent_launch_v55` and
`validate_agent_identity_binding_v55`. Enterprise validation uses the current Web Session, directory
state and grant. Partner validation uses subject, organization, operator and partner FAE grant state.
Keep v52 functions until the application switches, then revoke their execute grants without deleting
historical rows.

- [ ] **Step 4: Generalize Python launch types and routes**

```python
@dataclass(frozen=True)
class ExchangedAgentSubject:
    subject_id: UUID
    subject_type: Literal["enterprise_member", "partner_operator"]
    identity_binding_id: UUID
    agent_id: str
    internal_user_id: UUID | None = None
    display_name: str | None = None
    partner_display_name: str | None = None
```

`issue_enterprise(context)` retains the current authorization path. `issue_partner(subject_id)` accepts
only a subject already resolved by `PartnerService`, issues no Platform Cookie and writes no Provider
identity to the URL. Exchange and validate return the exact fields above plus `active` for validate.
The opaque code and redirect URL contain no display data. `display_name` and `partner_display_name` are
safe Platform-managed projections returned only over the private back-channel; enterprise subjects have
`partner_display_name=None`.
Before issuing or validating a partner Binding, `PartnerService` decrypts the mapped Provider subject only
inside the provider-call boundary and invokes `check_subject`. `inactive` revokes the Binding and returns
403; provider timeout/error returns the stable 503 `partner_identity_unavailable`; neither path falls back
to cached allow or anonymous access. Provider subjects are never attached to exceptions, metrics or audit
payloads.

- [ ] **Step 5: Run migration and launch tests**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest \
  tests/test_agent_launch.py \
  tests/test_agent_launch_migration.py \
  tests/test_generic_agent_launch_migration.py \
  tests/test_control_plane_migration.py -q
```

Expected: PASS for existing enterprise and new partner cases.

- [ ] **Step 6: Commit Task 5**

```bash
git add backend/control_migrations/055_generic_agent_launch_bindings.sql \
  backend/app/control_plane/agent_launch.py \
  backend/app/control_plane/routes_partner.py \
  backend/tests/test_agent_launch.py \
  backend/tests/test_generic_agent_launch_migration.py \
  backend/tests/test_control_plane_migration.py
git commit -m "feat(identity): generalize FAE subject launch"
```

### Task 6: Generalize FAE authenticated browser sessions without invalidating enterprise users

**Files:**
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/migrations/009_fae_authenticated_sessions.sql`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/platform_identity/models.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/platform_identity/service.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/platform_identity/client.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/platform_identity/routes.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/config.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/deploy/env.production.example`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_platform_identity.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_config.py`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_authenticated_session_migration.py`

**Interfaces:**
- Consumes: generic Platform exchange/validate response from Task 5.
- Produces: `PlatformSubject(subject_id, subject_type, internal_user_id, binding_id, agent_id, active)` and `AuthenticatedSessionService`.

- [ ] **Step 1: Write failing migration and service tests**

```python
@pytest.mark.parametrize(
    ("subject_type", "internal_user_id"),
    [("enterprise_member", ENTERPRISE_ID), ("partner_operator", None)],
)
async def test_authenticated_session_accepts_both_platform_subject_types(
    service, platform_client, subject_type, internal_user_id
) -> None:
    platform_client.exchange_result = PlatformSubject(
        subject_id=ENTERPRISE_ID if internal_user_id else PARTNER_ID,
        subject_type=subject_type,
        internal_user_id=internal_user_id,
        identity_binding_id=BINDING_ID,
        agent_id="ai-fae-agent",
        active=True,
    )
    issued = await service.exchange_launch("a" * 32)
    assert issued.subject.subject_type == subject_type
```

Add a regression test that an existing v8 enterprise session token remains valid after v9 and a test
that Partner validation failure never returns `public_customer`. Route tests assert `/partner/login`
returns 404 while unavailable and redirects only to the exact configured Platform start endpoint when
available.

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest \
  tests/unit/test_platform_identity.py \
  tests/unit/test_authenticated_session_migration.py -q
```

Expected: FAIL because `PlatformSubject` requires `internal_user_id` and the table is enterprise-only.

- [ ] **Step 3: Implement migration 009**

Rename `fae_enterprise_sessions` to `fae_authenticated_sessions`; add `owner_subject_id` and
`owner_subject_type`; backfill enterprise rows from `internal_user_id`; make `internal_user_id` nullable;
and add the exact enterprise/partner shape check from Task 5. Preserve token hashes, CSRF hashes,
Binding IDs, expiry timestamps and existing rows.

- [ ] **Step 4: Implement generic identity models and service**

```python
SubjectType = Literal["enterprise_member", "partner_operator"]


@dataclass(frozen=True)
class PlatformSubject:
    subject_id: UUID
    subject_type: SubjectType
    identity_binding_id: UUID
    agent_id: str
    active: bool
    internal_user_id: UUID | None = None
    display_name: str | None = None
    partner_display_name: str | None = None


@dataclass
class AuthenticatedSessionRecord:
    session_id: UUID
    session_token_hash: bytes
    session_token_key_version: int
    csrf_token_hash: bytes
    csrf_token_key_version: int
    owner_subject_id: UUID
    owner_subject_type: SubjectType
    internal_user_id: UUID | None
    identity_binding_id: UUID
    agent_id: str
    created_at: datetime
    last_seen_at: datetime
    last_validated_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
```

Rename service/repository classes but keep import aliases for one release. Keep the existing cookie name,
keyring file format, token derivation strings and CSRF header. Exchange/validate compare subject ID, type,
optional enterprise ID, Binding and Agent ID exactly. The authentication middleware writes the generic
subject to `request.state.platform_identity` and, for one compatibility release, also assigns the same
object to `request.state.enterprise_identity`; no route may construct either value from browser fields.

Make `PLATFORM_IDENTITY_ENABLED` and `config.platform_identity_enabled` the canonical generic switch.
Accept legacy `PLATFORM_ENTERPRISE_IDENTITY_ENABLED` for one release only when the new variable is absent;
if both are present with different values, fail configuration loading. Keep a read-only Python property
alias for existing callers during this task, migrate `server.py` to the generic field, and document the
deprecation in `env.production.example`.

Add exact `GET /partner/login`: when partner login is unavailable it returns 404; when available it
returns a 302 only to validated `PLATFORM_PARTNER_AUTH_START_URL`. Production validation accepts exactly
`https://agent.orbbec.com.cn/partner-auth/start`; query strings, fragments, credentials, alternate hosts
and scheme downgrade are rejected. The frontend never constructs this cross-origin target itself.

- [ ] **Step 5: Run Task 6 tests**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest \
  tests/unit/test_platform_identity.py \
  tests/unit/test_platform_identity_migration.py \
  tests/unit/test_authenticated_session_migration.py \
  tests/unit/test_config.py -q
```

Expected: PASS; old enterprise rows and tokens remain valid.

- [ ] **Step 6: Commit Task 6 in the FAE worktree**

```bash
git add migrations/009_fae_authenticated_sessions.sql \
  src/platform_identity/models.py src/platform_identity/service.py \
  src/platform_identity/client.py src/platform_identity/routes.py src/config.py \
  deploy/env.production.example \
  tests/unit/test_platform_identity.py \
  tests/unit/test_authenticated_session_migration.py tests/unit/test_config.py
git commit -m "feat(identity): generalize FAE authenticated sessions"
```

### Task 7: Make authenticated FAE conversations durable and restorable across devices

**Files:**
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/migrations/010_fae_authenticated_conversations.sql`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/src/storage/authenticated_conversations.py`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_authenticated_conversations.py`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_authenticated_conversation_migration.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/agent/session.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/api/server.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/config.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/storage/postgres_data_flywheel.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/deploy/env.production.example`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/deploy/docker-compose.prod.yml`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/deploy/scripts/bootstrap_enterprise_identity.sh`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_deploy_artifacts.py`

**Interfaces:**
- Consumes: `PlatformSubject` from Task 6 and existing `TaskContentCodec` AES-GCM pattern.
- Produces: `AuthenticatedConversationRepository.list_for_subject`, `load_for_subject`, `save_turn_and_checkpoint`.

- [ ] **Step 1: Write failing durability tests**

```python
def test_authenticated_conversation_restores_after_empty_memory_cache(repository, codec) -> None:
    original = Session(
        session_id=str(uuid4()),
        channel="fae",
        created_at=1.0,
        last_active=2.0,
        authentication_mode="platform_partner",
        owner_subject_id=str(PARTNER_ID),
    )
    original.append_message("user", "Gemini 335L 如何配置？")
    original.append_message("assistant", "先确认 SDK 版本。")
    repository.save_turn_and_checkpoint(original)
    restored = repository.load_for_subject(original.session_id, PARTNER_ID)
    assert restored.messages == original.messages
    assert restored.owner_subject_id == str(PARTNER_ID)


def test_cross_subject_restore_does_not_reveal_existence(repository) -> None:
    with pytest.raises(ConversationNotFound, match="conversation_not_found"):
        repository.load_for_subject(SESSION_ID, OTHER_SUBJECT_ID)
```

Add tests for cursor pagination, checkpoint tamper failure, key rotation, public anonymous exclusion and
session ID immutability.

- [ ] **Step 2: Run tests and verify failure**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest \
  tests/unit/test_authenticated_conversations.py \
  tests/unit/test_authenticated_conversation_migration.py -q
```

Expected: FAIL because authenticated conversation repository and migration 010 are absent.

- [ ] **Step 3: Implement migration 010**

Add `owner_subject_id uuid` and `owner_subject_type text` to `chat_sessions`, backfill authenticated
enterprise rows from current `user_id`, and add an authenticated-owner index ordered by
`last_active_at desc, id desc`. Add:

```sql
create table if not exists chat_session_checkpoints (
  external_session_id text primary key
    references chat_sessions(external_session_id) on delete cascade,
  owner_subject_id uuid not null,
  state_ciphertext bytea not null,
  state_key_version integer not null check (state_key_version > 0),
  state_sha256 bytea not null check (octet_length(state_sha256)=32),
  message_count integer not null check (message_count >= 0),
  updated_at timestamptz not null default clock_timestamp()
);
```

Database triggers reject owner changes after insert and require checkpoint owner to match the parent
session. Before migration, run a read-only preflight that counts enterprise rows whose `user_id` is null
or not a UUID; any non-zero result stops release for manual classification instead of coercing ownership.

- [ ] **Step 4: Implement deterministic checkpoint serialization**

Add `Session.to_checkpoint()` and `Session.from_checkpoint()` with an exact versioned JSON envelope:

```python
return {
    "version": 1,
    "session_id": self.session_id,
    "channel": self.channel,
    "authentication_mode": self.authentication_mode,
    "owner_subject_id": self.owner_subject_id,
    "messages": list(self.messages),
    "session_context": serialize_session_context(self.session_context),
    "active_attachment_ids": list(self.active_attachment_ids),
}
```

`serialize_session_context` enumerates every `SessionContext` dataclass field explicitly; it serializes
`current_schema` with `model_dump(mode="json")` only when that Pydantic object exists. The inverse parser
uses `extra="forbid"` checkpoint models, reconstructs `RequestSchema` and `SessionContext`, rejects unknown
versions/fields and never uses `pickle`, dynamic imports or arbitrary object hooks.

Seal with AES-GCM using AAD `fae-conversation:<external_session_id>:v<key_version>`. Add
`PLATFORM_AUTHENTICATED_CONTENT_KEYRING_FILE`; it is required when Platform identity is enabled. File loading
uses absolute path, `O_NOFOLLOW`, root/service ownership and mode `0600`, matching existing keyring rules.
Extend `bootstrap_enterprise_identity.sh` to create a distinct
`authenticated-content-keyring.json` without replacing the existing session keyring, mount it read-only in
`docker-compose.prod.yml`, and verify both files independently. Never reuse the task-content or browser
session key as conversation-content key material.

- [ ] **Step 5: Implement repository pagination and atomic save**

Refactor `PostgresDataFlywheelStore` so its existing session/turn insertion logic is available as a private
`_record_chat_turn_locked(cursor, record, attachment_relations)` operation. Public anonymous recording
continues to open its own transaction. `AuthenticatedConversationRepository.save_turn_and_checkpoint`
opens one transaction, calls that shared locked operation, writes the owner projection and encrypted
checkpoint, then commits; it must not duplicate the `chat_turns` SQL or create a second conversation SoR.
`load_for_subject` compares owner in SQL before decrypting. Cursor is opaque base64url of
`(last_active_at, id)` and limit is 1–50.

- [ ] **Step 6: Run Task 7 tests**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest \
  tests/unit/test_authenticated_conversations.py \
  tests/unit/test_authenticated_conversation_migration.py \
  tests/unit/test_session.py tests/unit/test_config.py \
  tests/unit/test_deploy_artifacts.py -q
```

Expected: PASS, including cache-loss restoration and cross-subject denial.

- [ ] **Step 7: Commit Task 7**

```bash
git add migrations/010_fae_authenticated_conversations.sql \
  src/storage/authenticated_conversations.py src/agent/session.py \
  src/storage/postgres_data_flywheel.py src/api/server.py src/config.py \
  deploy/env.production.example deploy/docker-compose.prod.yml \
  deploy/scripts/bootstrap_enterprise_identity.sh \
  tests/unit/test_authenticated_conversations.py \
  tests/unit/test_authenticated_conversation_migration.py \
  tests/unit/test_session.py tests/unit/test_config.py tests/unit/test_deploy_artifacts.py
git commit -m "feat(session): persist authenticated FAE conversations"
```

### Task 8: Enforce generic subject ownership across chat, history, Feedback and attachments

**Files:**
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/api/routes.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/api/attachment_routes.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/attachments/models.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/attachments/service.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/attachments/store.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/storage/postgres_data_flywheel.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_enterprise_chat_ownership.py`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_partner_chat_ownership.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_attachment_routes.py`

**Interfaces:**
- Consumes: `AuthenticatedConversationRepository` and generic `PlatformSubject`.
- Produces: subject-owned `/chat`, `/authenticated/conversations`, `/feedback` and `/attachments` behavior.

- [ ] **Step 1: Write failing partner ownership tests**

```python
def test_partner_can_continue_own_persisted_conversation_after_restart(app_factory) -> None:
    first = app_factory(subject=partner_subject(PARTNER_A))
    session_id = complete_chat(first, "介绍 Gemini 335L")["session_id"]
    second = app_factory(subject=partner_subject(PARTNER_A), empty_memory_store=True)
    response = second.post("/chat", json={"session_id": session_id, "message": "继续"})
    assert response.status_code == 200


def test_partner_cannot_probe_another_subjects_conversation(app_factory) -> None:
    session_id = seed_authenticated_conversation(PARTNER_A)
    response = app_factory(subject=partner_subject(PARTNER_B)).get(
        f"/authenticated/conversations/{session_id}"
    )
    assert response.status_code == 404
```

Add equivalent attachment bind/status/delete and Feedback target tests. Assert that Provider raw identity
never enters data-flywheel rows or attachment manifests.

- [ ] **Step 2: Run ownership tests to verify failure**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest \
  tests/unit/test_partner_chat_ownership.py \
  tests/unit/test_enterprise_chat_ownership.py \
  tests/unit/test_attachment_routes.py -q
```

Expected: FAIL because current code reads `subject.internal_user_id` and in-memory Session only.

- [ ] **Step 3: Replace enterprise-only ownership helpers**

```python
def _authenticated_subject(request: Request) -> PlatformSubject | None:
    return getattr(request.state, "platform_identity", None)


def _assert_session_owner(session: Session, request: Request) -> None:
    subject = _authenticated_subject(request)
    if session.authentication_mode == "public_customer":
        if subject is not None:
            raise HTTPException(403, "session access denied")
        return
    if subject is None or session.owner_subject_id != str(subject.subject_id):
        raise HTTPException(404, "session not found")
```

New authenticated sessions use `platform_enterprise` or `platform_partner` according to
`subject.subject_type`. Cache miss invokes `load_for_subject`; anonymous sessions remain in-memory.

- [ ] **Step 4: Add paginated authenticated conversation endpoints**

```text
GET /authenticated/conversations?cursor=<opaque>&limit=30
GET /authenticated/conversations/{external_session_id}
```

Both require a valid authenticated FAE cookie. List returns ID, title, channel and timestamps only; detail
returns persisted messages and attachment projections owned by the same subject. Invalid cursors return
400; no authentication returns 401; wrong owner returns 404.

- [ ] **Step 5: Generalize attachment ownership with backward-compatible manifests**

Rename in-memory fields and new manifests to `owner_subject_id`. `AttachmentManifest.from_dict` accepts
legacy `owner_internal_user_id` only when `owner_subject_id` is absent, then writes back the generic field
on the next mutation. Upload, status, delete and chat binding compare the generic subject ID. Public
anonymous attachments retain owner `None` and cannot be claimed by a logged-in subject.

- [ ] **Step 6: Update data-flywheel ownership projection**

Write `chat_sessions.owner_subject_id` and `owner_subject_type` for authenticated sessions. Preserve
`chat_sessions.user_id=internal_user_id` for enterprise subjects and leave it null for partner subjects.
Do not write Partner identifiers to `external_user_id`.

- [ ] **Step 7: Run Task 8 tests**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest \
  tests/unit/test_partner_chat_ownership.py \
  tests/unit/test_enterprise_chat_ownership.py \
  tests/unit/test_attachment_routes.py \
  tests/unit/test_attachment_store.py \
  tests/unit/test_platform_identity.py -q
```

Expected: PASS for enterprise, partner and anonymous ownership matrices.

- [ ] **Step 8: Commit Task 8**

```bash
git add src/api/routes.py src/api/attachment_routes.py \
  src/attachments/models.py src/attachments/service.py src/attachments/store.py \
  src/storage/postgres_data_flywheel.py \
  tests/unit/test_partner_chat_ownership.py \
  tests/unit/test_enterprise_chat_ownership.py \
  tests/unit/test_attachment_routes.py tests/unit/test_attachment_store.py
git commit -m "feat(identity): enforce FAE subject ownership"
```

### Task 9: Add partner-aware FAE identity UX and authenticated history

**Files:**
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/webui/src/enterpriseIdentity.ts`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/webui/src/enterpriseIdentity.test.ts`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/webui/src/AuthenticatedSessionNav.tsx`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/webui/src/AuthenticatedSessionNav.test.tsx`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/webui/src/api.ts`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/webui/src/App.tsx`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/webui/src/AppRender.test.tsx`

**Interfaces:**
- Consumes: FAE authenticated identity response and conversation endpoints from Tasks 6–8.
- Produces: partner login link gating, account projection and paginated cross-device conversation navigation.

- [ ] **Step 1: Write failing identity bootstrap tests**

```typescript
it("accepts a partner launch without exposing provider identity", async () => {
  window.location.hash = "#partner_launch=abcdefghijklmnopqrstuvwxyz123456";
  fetchMock.mockResolvedValue(new Response(JSON.stringify({
    authenticated: true,
    authentication_mode: "platform_partner",
    display_name: "坐席一",
    partner_display_name: "合作方甲",
    csrf_token: "csrf",
  }), { status: 201, headers: { "Content-Type": "application/json" } }));
  expect(await bootstrapEnterpriseIdentity()).toBe("platform_partner");
  expect(currentAuthenticatedAccount()).toEqual({
    mode: "platform_partner",
    displayName: "坐席一",
    partnerDisplayName: "合作方甲",
  });
  expect(JSON.stringify(currentAuthenticatedAccount())).not.toContain("provider_subject");
});
```

Add tests that the partner login link is absent when capability is false, no preparing placeholder appears,
Platform failure is not treated as public mode and both `platform_launch` and `partner_launch` are removed
from the URL fragment before rendering.

- [ ] **Step 2: Write failing history navigation tests**

```tsx
it("paginates owned conversations and restores one in place", async () => {
  render(<AuthenticatedSessionNav onOpen={onOpen} />);
  expect(await screen.findByText("Gemini 335L 配置")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "加载更多" }));
  expect(fetchMock).toHaveBeenCalledWith(
    "/authenticated/conversations?cursor=next-1&limit=30",
    expect.any(Object),
  );
  await userEvent.click(screen.getByText("Gemini 335L 配置"));
  expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ sessionId: "session-1" }));
});
```

- [ ] **Step 3: Run frontend tests and verify failure**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent/webui
npm test -- enterpriseIdentity.test.ts AuthenticatedSessionNav.test.tsx AppRender.test.tsx
```

Expected: FAIL because partner mode and authenticated history component do not exist.

- [ ] **Step 4: Extend identity state without creating a second FAE UI**

```typescript
export type EnterpriseAuthenticationMode =
  | "public_customer"
  | "platform_enterprise"
  | "platform_partner";

export type AuthenticatedAccount = {
  mode: Exclude<EnterpriseAuthenticationMode, "public_customer">;
  displayName: string;
  partnerDisplayName: string | null;
};

export function currentAuthenticatedAccount(): AuthenticatedAccount | null {
  return authenticatedAccount;
}
```

Use the same chat component, composer, attachments, sources, Feedback and error handling for enterprise and
partner. The account menu displays only the provided projections. “合作方客服登录” is a normal link to
FAE's own `/partner/login` route and is rendered only when `/identity/capabilities` returns
`partner_login_available=true`.

- [ ] **Step 5: Implement authenticated history navigation**

Render the paginated list only for authenticated modes. Opening a conversation replaces current messages
and Session ID in the existing workspace, so the next `/chat` continues that persisted conversation. A new
conversation clears only current UI state; it does not archive or delete old history.

- [ ] **Step 6: Run frontend tests and production build**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent/webui
npm test -- enterpriseIdentity.test.ts AuthenticatedSessionNav.test.tsx AppRender.test.tsx
npm run build
```

Expected: PASS and build success.

- [ ] **Step 7: Commit Task 9**

```bash
git add webui/src/enterpriseIdentity.ts webui/src/enterpriseIdentity.test.ts \
  webui/src/AuthenticatedSessionNav.tsx \
  webui/src/AuthenticatedSessionNav.test.tsx \
  webui/src/api.ts webui/src/App.tsx webui/src/AppRender.test.tsx
git commit -m "feat(web): add authenticated FAE history"
```

### Task 10: Freeze the cross-repository identity contract and capability parity

**Files:**
- Create: `contracts/fae_identity_v1/pyproject.toml`
- Create: `contracts/fae_identity_v1/schema/fae-identity-v1.schema.json`
- Create: `contracts/fae_identity_v1/fixtures/enterprise.json`
- Create: `contracts/fae_identity_v1/fixtures/partner.json`
- Create: `contracts/fae_identity_v1/tests/test_contract.py`
- Create: `backend/tests/test_fae_identity_contract_asset.py`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/tests/contract/conftest.py`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/tests/contract/test_platform_identity_v1.py`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_identity_capability_parity.py`

**Interfaces:**
- Consumes: Platform v55 exchange/validate and FAE generic `PlatformSubject`.
- Produces: `orbbec-fae-identity/v1` JSON contract and a pinned commit/SHA-256 handoff between repositories.

- [ ] **Step 1: Write failing contract asset tests**

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parents[2] / "contracts/fae_identity_v1/fixtures"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_exchange_contract_has_only_minimal_generic_subject_fields() -> None:
    payload = load_fixture("partner.json")["exchange"]
    assert set(payload) == {
        "contract_version", "subject_id", "subject_type",
        "internal_user_id", "identity_binding_id", "agent_id",
        "display_name", "partner_display_name",
    }
    assert payload["contract_version"] == "orbbec-fae-identity/v1"
    assert payload["subject_type"] == "partner_operator"
    assert payload["internal_user_id"] is None
```

The parity test instantiates enterprise and partner sessions and asserts equality of model ID, Loop budget,
capability registry, Tool schemas, knowledge repositories and attachment limits.

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest tests/test_fae_identity_contract_asset.py -q
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest \
  tests/contract/test_platform_identity_v1.py \
  tests/unit/test_identity_capability_parity.py -q
```

Expected: FAIL because the shared contract assets are absent.

- [ ] **Step 3: Create the JSON Schema and fixtures**

The Schema permits only `enterprise_member` and `partner_operator`; requires `internal_user_id` to be a UUID
for enterprise and null for partner; fixes `agent_id` to `ai-fae-agent`; fixes contract version to
`orbbec-fae-identity/v1`; and uses `additionalProperties:false` for exchange, validate and capability
responses. It permits only the safe Platform-managed `display_name` and `partner_display_name`
projections needed by the account menu; department, role, Provider ID, Cookie, Token and CSRF fields are
illegal. Contract tests prove names exist only in the private exchange/validate response and never in the
Launch Code, redirect URL, access log or audit payload.

- [ ] **Step 4: Add contract consumption and parity tests**

`tests/contract/conftest.py` requires an absolute `FAE_IDENTITY_CONTRACT_ROOT`, verifies that
`FAE_IDENTITY_CONTRACT_COMMIT` names an existing commit, and requires the current
`contracts/fae_identity_v1` subtree to be byte-identical to that commit. It then hashes the schema plus
fixtures in sorted relative-path order and compares the lowercase digest with
`FAE_IDENTITY_CONTRACT_SHA256`. It rejects dirty changes inside the contract subtree, missing environment
values, relative paths and hash/commit mismatches before collecting any contract test; later unrelated
Platform commits do not invalidate the pin. FAE tests load the
Platform fixture from that verified checkout. Platform tests validate every fixture
against the Schema. Parity tests compare configuration objects before a model request, not nondeterministic
answer text.

- [ ] **Step 5: Run contract and parity tests**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
backend/.venv/bin/python -m pytest contracts/fae_identity_v1/tests backend/tests/test_fae_identity_contract_asset.py -q
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest \
  tests/contract/test_platform_identity_v1.py \
  tests/unit/test_identity_capability_parity.py -q
```

Expected: PASS in both repositories.

- [ ] **Step 6: Commit Platform contract assets**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
git add contracts/fae_identity_v1 backend/tests/test_fae_identity_contract_asset.py
git commit -m "test(contract): freeze FAE identity v1"
```

- [ ] **Step 7: Commit FAE contract consumption**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
git add tests/contract/test_platform_identity_v1.py \
  tests/contract/conftest.py \
  tests/unit/test_identity_capability_parity.py
git commit -m "test(contract): consume FAE identity v1"
```

### Task 11: Add production-disable gates, provider probe evidence and rollback checks

**Files:**
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/accept.sh`
- Create: `deploy/cloud/fae-partner-provider.release.schema.json`
- Create: `backend/app/control_plane/partner_release.py`
- Create: `backend/tests/test_partner_release.py`
- Create: `docs/runbooks/fae-partner-provider-probe.md`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/deploy/env.production.example`
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_partner_login_gate.py`

**Interfaces:**
- Consumes: Provider-neutral foundation and Reference Provider.
- Produces: fail-closed production configuration, evidence schema and acceptance checks; no production Partner Provider.

- [ ] **Step 1: Write failing production-gate tests**

```python
def test_partner_login_stays_absent_without_signed_release(config_factory) -> None:
    config = config_factory(
        environment="production",
        partner_identity_enabled=True,
        partner_provider_release=None,
    )
    with pytest.raises(ValueError, match="partner_provider_release_required"):
        validate_partner_release(config)


def test_reference_provider_can_never_enable_production(config_factory) -> None:
    release = signed_release(provider_kind="reference")
    with pytest.raises(ValueError, match="partner_reference_provider_forbidden"):
        validate_partner_release(config_factory(environment="production", release=release))
```

FAE gate tests assert `/identity/capabilities` returns `partner_login_available=false` and a production
build rendered against that capability response exposes no partner login control in the DOM when the
release is absent. The test does not require the dormant component source to be absent from bundled code.

- [ ] **Step 2: Run tests and verify failure**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest tests/test_partner_release.py -q
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest tests/unit/test_partner_login_gate.py -q
```

Expected: FAIL because release validation and gates are absent.

- [ ] **Step 3: Define the provider evidence release**

The JSON Schema requires:

```json
{
  "contract_version": "orbbec-fae-partner-provider/v1",
  "provider_kind": "registered-non-reference-adapter",
  "stable_subject_verified": true,
  "two_distinct_subjects_verified": true,
  "active_status_or_local_revocation_verified": true,
  "shared_password_forbidden": true,
  "state_and_callback_replay_verified": true,
  "dev_real_account_tested_at": "RFC3339 timestamp",
  "evidence_sha256": "64 lowercase hex characters"
}
```

`partner_release.py` validates the Schema, exact environment, file ownership/mode, release SHA and Provider
registration. Missing or invalid evidence keeps the feature off; it does not render a preparing page.

- [ ] **Step 4: Add exact acceptance and rollback assertions**

`accept.sh` must assert:

```text
PARTNER_PROVIDER_CONFIG_VALID=true
PARTNER_LOGIN_EXPECTED=false before provider-specific release
PUBLIC_FAE_CHAT_UNCHANGED=true
ENTERPRISE_FAE_LAUNCH_UNCHANGED=true
OFFICE_ROUTE_UNCHANGED=true
PLATFORM_ADMIN_ROUTE_UNCHANGED=true
```

The runbook captures pre-change container IDs, Image IDs, StartedAt, RestartCount, FAE public behavior,
Platform enterprise launch and `/office/`. Rollback revokes Partner Bindings and disables only Partner start
and callback routes; it does not delete partner rows or restart unrelated Agents.

- [ ] **Step 5: Run Task 11 tests**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest tests/test_partner_release.py tests/test_cloud_acceptance_policy.py -q
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest tests/unit/test_partner_login_gate.py -q
```

Expected: PASS and Partner login remains unavailable in production configuration.

- [ ] **Step 6: Commit Platform operational gates**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
git add deploy/cloud/compose.yaml \
  deploy/cloud/accept.sh deploy/cloud/fae-partner-provider.release.schema.json \
  backend/app/control_plane/partner_release.py backend/tests/test_partner_release.py \
  docs/runbooks/fae-partner-provider-probe.md
git commit -m "feat(deploy): gate FAE partner identity release"
```

- [ ] **Step 7: Commit FAE gate**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
git add deploy/env.production.example tests/unit/test_partner_login_gate.py
git commit -m "feat(identity): gate partner login availability"
```

### Task 12: Run full verification and produce the provider-selection handoff

**Files:**
- Create: `docs/reviews/2026-08-29-fae-partner-foundation-verification.md`
- Create: `docs/reviews/2026-08-29-fae-partner-provider-decision-input.md`
- Modify: `docs/superpowers/specs/2026-08-29-fae-partner-operator-access-design.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified foundation report and the exact input required for a provider-specific spec/plan.

- [ ] **Step 1: Run Platform backend verification**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest -q
```

Expected: all Platform backend tests PASS with zero failures.

- [ ] **Step 2: Run Platform frontend verification**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/webui
npm test
npm run build
```

Expected: all Vitest files PASS and the production build succeeds.

- [ ] **Step 3: Run FAE backend verification**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest -q
```

Expected: all FAE backend tests PASS with zero failures.

- [ ] **Step 4: Run FAE frontend verification**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent/webui
npm test
npm run build
```

Expected: all Vitest files PASS and the production build succeeds.

- [ ] **Step 5: Run a two-subject Reference Provider Dev scenario**

Use fixed test identities `partner-fixture-operator-a` and `partner-fixture-operator-b`. Prove:

```text
owner creates organization + operators A/B, with no implicit grants
operator A/B first login -> partner_binding_required -> owner links both requests and grants FAE
operator A login again -> FAE Launch -> create conversation -> upload image -> submit Feedback
operator B login -> cannot list/read/continue/delete A conversation or attachment
operator B creates own conversation
operator A logs in from a second browser -> lists and continues A conversation
owner suspends A -> A fails Binding validation within 60 seconds
operator B remains active
enterprise member still launches and sees the same FAE capability manifest
anonymous customer still chats without partner identity
```

Record only internal UUIDs, status codes, timestamps, release IDs and capability hashes. Do not record
tokens, raw Provider subjects, questions, answers or attachment names.

- [ ] **Step 6: Write verification and provider-decision reports**

The verification report includes exact commit IDs, test counts, build results, migration upgrade evidence,
Reference Provider scenario evidence, unchanged-route evidence and rollback outcome. The provider-decision
input states that production login is intentionally disabled and requests only these facts from the real
partner environment:

```text
candidate provider name
stable per-operator subject field
authorization flow
active/revoked status mechanism
two test operator accounts
required application type and permission package
callback allowlist requirements
token lifetime and refresh behavior
```

Update the design status to `Foundation implemented and verified; production Provider selection required`
only after all evidence exists.

- [ ] **Step 7: Commit verification documents**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
git add docs/reviews/2026-08-29-fae-partner-foundation-verification.md \
  docs/reviews/2026-08-29-fae-partner-provider-decision-input.md \
  docs/superpowers/specs/2026-08-29-fae-partner-operator-access-design.md
git commit -m "docs: verify FAE partner identity foundation"
```

---

## Review and Release Sequence

1. Implement Tasks 1–5 in an isolated Platform worktree; review each commit before continuing.
2. Implement Tasks 6–9 in an isolated FAE worktree; review each commit before continuing.
3. Freeze and consume the cross-repository contract in Task 10.
4. Add production-disable gates before any deployment in Task 11.
5. Run Task 12 and merge both local branches only after full green verification.
6. Deploy the foundation with partner login disabled; verify public FAE, enterprise FAE, Platform and
   `/office/*` invariance.
7. Use the provider-decision input with two real partner operators.
8. Write a provider-specific design and TDD plan from the measured result; only that later release may set
   `partner_login_available=true`.

## Design Coverage Map

| Approved design requirement | Plan task |
|---|---|
| Same FAE capability and knowledge for enterprise/partner | Tasks 8, 10, 12 |
| Data equality but private Session ownership | Tasks 7, 8 |
| Platform owns partner identity | Tasks 1–5 |
| Provider remains pluggable | Tasks 4, 11, 12 |
| No Platform access for partners | Tasks 3–5 |
| 60-second single-use Launch and Binding | Tasks 5, 6, 10 |
| Organization/operator/grant revocation | Tasks 2, 5, 12 |
| Unknown identity remains closed until owner binding | Tasks 2–4, 12 |
| PostgreSQL cross-device conversation continuity | Tasks 7–9 |
| Attachment and Feedback ownership | Task 8 |
| No raw Provider identity in FAE/logs | Tasks 2, 4, 5, 8, 10 |
| Public and enterprise paths unchanged | Tasks 6, 9, 11, 12 |
| Production remains closed without real Provider evidence | Tasks 4, 11, 12 |
| Real two-operator acceptance deferred until provider selection | Task 12 handoff and next provider-specific plan |
