from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, replace
from hashlib import sha256
from time import monotonic, sleep
from uuid import UUID, uuid4

import psycopg
import pytest
from app.control_plane.auth import WebSessionRepository
from app.control_plane.crypto import ProviderIdentityCodec
from app.control_plane.dingtalk import DingTalkAuthResult, DingTalkMember
from app.control_plane.directory import DirectoryReconciler
from app.control_plane.directory_worker import DirectoryWorkerRepository
from app.control_plane.identity import IdentityResolver
from app.control_plane.models import DirectoryFreshness, Role
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from test_control_plane_migration import ROLES
from test_dingtalk_identity import DirectoryClient, _stage_and_promote_generation
from test_directory_reconciliation import FakeClient
from test_identity_crypto import _codec
from test_identity_rate_limits import _attempt, _limiter

pytest_plugins = ("test_control_plane_migration",)
pytestmark = pytest.mark.postgres
FUNCTION = "platform_control.assign_roster_platform_admin_v128"


@dataclass
class Grant:
    env: dict
    actor: UUID
    generation: UUID
    key: UUID
    employee: DingTalkMember
    codec: ProviderIdentityCodec

    @property
    def target(self):
        return UUID(
            bytes=sha256(f"orbbec:roster-admin:v1:{self.generation}:{self.key}".encode()).digest()[:16]
        )

    @property
    def app_url(self):
        return self.env["urls"]["platform_control_app"]

    def intent(self, operation, *, actor=None, target=None, revoke=False, version=0):
        event = uuid4()
        details = {
            "operation_id": str(operation),
            "result": "requested",
            "expected_row_version": version,
            "previous_role": "platform_admin" if revoke else "member",
            "new_role": "member" if revoke else "platform_admin",
        }
        with psycopg.connect(self.env["urls"]["platform_audit_append"]) as db:
            db.execute(
                "select platform_control.append_audit_event(%s,%s,%s,'internal_user',%s,"
                "%s,'requested',%s,%s)",
                (
                    event,
                    actor or self.actor,
                    "admin_role_revocation_requested" if revoke else "admin_role_assignment_requested",
                    str(target or self.target),
                    operation,
                    "admin_access_revoked" if revoke else "admin_access_approved",
                    Jsonb(details),
                ),
            )
        return event

    def assign(self, operation, event, *, actor=None, key=None):
        with psycopg.connect(self.app_url) as db:
            return db.execute(
                f"select {FUNCTION}(%s,%s,%s,%s,%s)",
                (operation, actor or self.actor, key or self.key, event, self.generation),
            ).fetchone()[0]

    def identity_state(self):
        with psycopg.connect(self.env["admin"]) as db:
            return db.execute(
                "select (select count(*) from platform_control.internal_users),"
                "(select count(*) from platform_control.provider_identities),"
                "(select count(*) from platform_control.management_mutations),"
                "(select internal_user_id from platform_control.directory_members "
                "where generation_id=%s and member_key=%s),"
                "(select count(*) from platform_control.internal_users where internal_user_id=%s "
                "and role='platform_admin')",
                (self.generation, self.key, self.target),
            ).fetchone()

    def resolver(self):
        return IdentityResolver(
            self.app_url,
            corp_id="test-corp",
            client=DirectoryClient(self.employee),
            identity_codec=self.codec,
        )

    async def login(self):
        return await self.resolver().resolve_active_member(
            DingTalkAuthResult(self.employee.unionid, self.employee.userid, "test-corp"),
            DirectoryFreshness.FRESH,
        )


@pytest.fixture(scope="module")
def owner(control_database):
    env = control_database["environments"]["production"]
    owner_id = uuid4()
    with psycopg.connect(env["admin"]) as db:
        db.execute(
            "insert into platform_control.internal_users(internal_user_id,role,display_name,status)"
            " values(%s,'platform_owner','Test owner','active')",
            (owner_id,),
        )
    return env, owner_id


@pytest.fixture
def grant(owner, tmp_path):
    env, owner_id = owner
    member = DingTalkMember(uuid4().hex, uuid4().hex, "Roster member", True, (1,))
    codec = _codec(tmp_path)
    generation = _stage_and_promote_generation(env, codec, [member])
    with psycopg.connect(env["admin"]) as db:
        (key,) = db.execute(
            "select member_key from platform_control.directory_members where generation_id=%s",
            (generation,),
        ).fetchone()
    return Grant(env, owner_id, generation, key, member, codec)


def test_app_only_privilege_in_each_environment(control_database):
    for env in control_database["environments"].values():
        with psycopg.connect(env["admin"]) as db:
            signature = f"{FUNCTION}(uuid,uuid,uuid,uuid,uuid)"
            assert db.execute(
                "select to_regprocedure(%s) is null", (f"{FUNCTION}(uuid,uuid,uuid,uuid)",)
            ).fetchone() == (True,)
            assert db.execute("select to_regprocedure(%s) is not null", (signature,)).fetchone() == (True,)
            for role in (*ROLES, "public"):
                assert db.execute(
                    "select has_function_privilege(%s,%s,'execute')", (role, signature)
                ).fetchone() == (role == env["roles"][1],)


async def test_grant_login_revoke_and_replay_after_directory_change(grant):
    operation = uuid4()
    event = grant.intent(operation)
    result = grant.assign(operation, event)
    assert result == {
        "operation_id": str(operation),
        "previous_role": "member",
        "new_role": "platform_admin",
        "row_version": 1,
        "session_revocation_count": 0,
        "previous_scopes": [],
        "new_scopes": [],
    }
    assert await grant.login() == grant.target
    limiter = _limiter(grant.env)
    sessions = WebSessionRepository(
        grant.app_url, secrets=limiter.secrets, identity_codec=grant.codec, directory_id="test-corp"
    )
    challenge = limiter.issue_browser_challenge()
    attempt = _attempt(limiter, challenge=challenge)
    limiter.create_login_attempt(attempt, edge_ip="192.0.2.15")
    assert sessions.claim_attempt(
        state_digest=attempt.state_digest, environment="production", attempt_kind="qr"
    )
    token = {"token_digest": uuid4().bytes * 2, "token_key_version": 1, "idle_seconds": 28800}
    assert sessions.issue_session(
        attempt_id=attempt.attempt_id,
        internal_user_id=grant.target,
        csrf_digest=uuid4().bytes * 2,
        csrf_key_version=1,
        absolute_seconds=86400,
        **token,
    )
    assert sessions.authenticate_session(**token)[0].role is Role.PLATFORM_ADMIN
    revoke = uuid4()
    revoke_event = grant.intent(revoke, revoke=True, version=1)
    with psycopg.connect(grant.app_url) as db:
        removed = db.execute(
            "select platform_control.revoke_platform_admin(%s,%s,%s,1,%s)",
            (revoke, grant.actor, grant.target, revoke_event),
        ).fetchone()[0]
    assert removed["session_revocation_count"] == 1
    assert sessions.authenticate_session(**token) is None
    new_generation = _stage_and_promote_generation(grant.env, grant.codec, [grant.employee])
    with pytest.raises(psycopg.errors.UniqueViolation, match="operation identity collision"):
        replace(grant, generation=new_generation).assign(operation, event)
    before = grant.identity_state()
    assert grant.assign(operation, event) == result
    assert grant.identity_state() == before
    assert before[-1] == 0
    for key, audit in ((uuid4(), event), (grant.key, uuid4())):
        with pytest.raises(psycopg.errors.UniqueViolation, match="operation identity collision"):
            grant.assign(operation, audit, key=key)


@pytest.mark.parametrize(
    "problem",
    [
        "missing_generation",
        "stale",
        "warning",
        "nonemployee",
        "wrong_intent_actor",
        "future",
        "inactive",
        "missing_union",
        "missing_member",
        "wrong_owner",
        "missing_intent",
        "wrong_target",
        "wrong_version",
        "wrong_action",
        "wrong_operation",
    ],
)
def test_ineligible_grant_rolls_back(grant, problem):
    operation, key, actor = uuid4(), grant.key, grant.actor
    if problem == "missing_member":
        key = uuid4()
    audit_actor = grant.actor
    if problem in ("wrong_owner", "wrong_intent_actor"):
        other_actor = uuid4()
        with psycopg.connect(grant.env["admin"]) as db:
            db.execute(
                "insert into platform_control.internal_users(internal_user_id,role,display_name,status)"
                " values(%s,'platform_admin','Other actor','active')",
                (other_actor,),
            )
        audit_actor = other_actor
        if problem == "wrong_owner":
            actor = other_actor
    audit_target = UUID(
        bytes=sha256(f"orbbec:roster-admin:v1:{grant.generation}:{key}".encode()).digest()[:16]
    )
    event = grant.intent(
        uuid4() if problem == "wrong_operation" else operation,
        target=uuid4() if problem == "wrong_target" else audit_target,
        actor=audit_actor,
        version=1 if problem == "wrong_version" else 0,
        revoke=problem == "wrong_action",
    )
    if problem == "missing_intent":
        event = uuid4()
    with psycopg.connect(grant.env["admin"]) as db:
        if problem == "missing_generation":
            db.execute("update platform_control.directory_state set active_generation_id=null")
        if problem == "nonemployee":
            db.execute(
                "update platform_control.directory_members set subject_kind='visitor' where generation_id=%s",
                (grant.generation,),
            )
        if problem in ("warning", "stale", "future"):
            db.execute(
                "update platform_control.directory_state set last_complete_at=now()+%s::interval",
                ({"warning": "-8 hours", "stale": "-24 hours", "future": "1 hour"}[problem],),
            )
        if problem == "inactive":
            db.execute(
                "update platform_control.directory_members set status='inactive' where generation_id=%s",
                (grant.generation,),
            )
        if problem == "missing_union":
            db.execute(
                "update platform_control.directory_members set union_encrypted_provider_id=null,"
                "union_encryption_key_version=null where generation_id=%s",
                (grant.generation,),
            )
    before = grant.identity_state()
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege)):
        grant.assign(operation, event, actor=actor, key=key)
    assert grant.identity_state() == before


def test_concurrent_first_login_commits_while_owner_waits(grant, monkeypatch):
    operation = uuid4()
    event = grant.intent(operation)
    before = grant.identity_state()
    resolver = grant.resolver()
    # Hold the real login resolver's transaction open until the grant is waiting on it.
    with psycopg.connect(grant.app_url, row_factory=dict_row) as login_db:
        monkeypatch.setattr(resolver, "_connection", lambda: nullcontext(login_db))
        linked = resolver._resolve_transaction(grant.employee)
        assert linked != grant.target
        with ThreadPoolExecutor(max_workers=1) as pool:
            assignment = pool.submit(grant.assign, operation, event)
            try:
                deadline = monotonic() + 3
                while True:
                    blocked = login_db.execute(
                        "select exists(select 1 from pg_locks l where not l.granted "
                        "and pg_backend_pid()=any(pg_blocking_pids(l.pid))) as waiting"
                    ).fetchone()["waiting"]
                    if blocked:
                        break
                    assert monotonic() < deadline, "grant failed to wait for concurrent login"
                    sleep(0.01)
            finally:
                login_db.commit()
            with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation)):
                assignment.result(timeout=3)
    accounts, mappings, ledger, member_account, admin_count = grant.identity_state()
    assert (accounts, mappings, ledger) == (before[0] + 1, before[1] + 2, before[2])
    assert (member_account, admin_count) == (linked, 0)
    with psycopg.connect(grant.env["admin"]) as db:
        assert db.execute(
            "select role from platform_control.internal_users where internal_user_id=%s", (linked,)
        ).fetchone() == ("member",)


@pytest.mark.parametrize(
    "mapping", ["corporate", "union", "both", "existing_target", "existing_target_both"]
)
def test_unlinked_roster_with_colliding_account_or_mappings_is_rejected(grant, mapping):
    operation = uuid4()
    event = grant.intent(operation)
    with psycopg.connect(grant.env["admin"]) as db:
        account = grant.target if mapping.startswith("existing_target") else uuid4()
        db.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values(%s,'Existing account','active')",
            (account,),
        )
        for channel, kind in (("corporate", "employee"), ("union", "employee_union")):
            if mapping in (channel, "both", "existing_target_both"):
                value = (
                    grant.employee.unionid
                    if channel == "union"
                    else IdentityResolver.corporate_provider_id("test-corp", grant.employee.userid)
                )
                protected = grant.codec.seal(kind, value)
                db.execute(
                    "insert into platform_control.provider_identities(provider_identity_id,internal_user_id,"
                    "subject_kind,lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version)"
                    "values(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        account,
                        kind,
                        protected.lookup_hmac,
                        protected.lookup_key_version,
                        protected.ciphertext,
                        protected.encryption_key_version,
                    ),
                )
    before = grant.identity_state()
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation)):
        grant.assign(operation, event)
    assert grant.identity_state() == before


@pytest.mark.parametrize("same_department_label", [True, False])
def test_namesakes_need_distinguishable_department_labels(grant, same_department_label):
    # The two departments deliberately have different IDs. Owners see their labels.
    operation = uuid4()
    event = grant.intent(operation)
    with psycopg.connect(grant.env["admin"]) as db:
        second_key, department = uuid4(), uuid4()
        db.execute(
            "insert into platform_control.directory_members(generation_id,member_key,subject_kind,"
            "lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status)"
            " values(%s,%s,'employee',%s,2,%s,2,%s,'active')",
            (
                grant.generation,
                second_key,
                uuid4().bytes * 2,
                uuid4().bytes * 2,
                grant.employee.display_name,
            ),
        )
        db.execute(
            "insert into platform_control.directory_departments(generation_id,department_key,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version,display_name)"
            " values(%s,%s,%s,2,%s,2,%s)",
            (
                grant.generation,
                department,
                uuid4().bytes * 2,
                uuid4().bytes * 2,
                "Organization" if same_department_label else "Another team",
            ),
        )
        db.execute(
            "insert into platform_control.member_departments values(%s,%s,%s)",
            (grant.generation, second_key, department),
        )
    if same_department_label:
        before = grant.identity_state()
        with pytest.raises(psycopg.errors.CheckViolation):
            grant.assign(operation, event)
        assert grant.identity_state() == before
    else:
        assert grant.assign(operation, event)["new_role"] == "platform_admin"


def test_terminal_failure_prevents_both_provisioning_and_grant(grant):
    operation = uuid4()
    event = grant.intent(operation)
    with psycopg.connect(grant.env["urls"]["platform_audit_append"]) as db:
        db.execute(
            "select platform_control.append_audit_event(%s,%s,'admin_role_assignment_failed',"
            "'internal_user',%s,%s,'failed','admin_access_approved',%s)",
            (
                uuid4(),
                grant.actor,
                str(grant.target),
                operation,
                Jsonb(
                    {
                        "linked_audit_event_id": str(event),
                        "operation_id": str(operation),
                        "error_code": "business_rejected",
                        "result": "failed",
                    }
                ),
            ),
        )
    before = grant.identity_state()
    with pytest.raises(psycopg.errors.CheckViolation, match="operation already terminal failed"):
        grant.assign(operation, event)
    assert grant.identity_state() == before


async def test_new_union_for_same_corporate_user_requires_new_generation_selection(grant):
    class RosterProvider(FakeClient):
        employee = grant.employee

        async def iter_department_members(self, department_id):
            if department_id == 1:
                yield self.employee

        async def get_member(self, userid):
            assert userid == self.employee.userid
            return self.employee

    provider = RosterProvider()
    worker = DirectoryReconciler(
        provider,
        DirectoryWorkerRepository(grant.env["urls"]["platform_directory_worker"]),
        grant.codec,
        corp_id="test-corp",
    )

    async def publish_and_select():
        published = await worker.run_full()
        with psycopg.connect(grant.app_url) as db:
            generation, key = db.execute(
                "select directory_generation_id,directory_member_id from "
                "platform_control.read_office_recipient_directory_v54("
                "'search',%s,'{}',false,10,null,'{}','{}') where row_kind='member'",
                (provider.employee.display_name,),
            ).fetchone()
        assert generation == published.generation_id
        return replace(grant, generation=generation, key=key, employee=provider.employee)

    original = await publish_and_select()
    request = uuid4()
    intent = original.intent(request)
    provider.employee = replace(provider.employee, unionid=uuid4().hex)
    current = await publish_and_select()
    assert current.key == original.key and current.generation != original.generation

    before = current.identity_state()
    with pytest.raises(psycopg.errors.CheckViolation):
        original.assign(request, intent)
    assert current.identity_state() == before
    with pytest.raises(psycopg.errors.CheckViolation, match="matching audit intent required"):
        current.assign(request, intent)
    assert current.target != original.target
    refreshed = uuid4()
    assert current.assign(refreshed, current.intent(refreshed))["new_role"] == "platform_admin"
    assert await current.login() == current.target


def test_null_generation_rejected(grant):
    operation = uuid4()
    event = grant.intent(operation)
    before = grant.identity_state()
    with (
        psycopg.connect(grant.app_url) as db,
        pytest.raises(psycopg.errors.CheckViolation, match="admin assignment precondition invalid"),
    ):
        db.execute(f"select {FUNCTION}(%s,%s,%s,%s,null)", (operation, grant.actor, grant.key, event))
    assert grant.identity_state() == before
