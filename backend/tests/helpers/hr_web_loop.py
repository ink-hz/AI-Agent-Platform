"""Owned HTTP processes with real persisted browser identity and conversation state.

The initial regression uses the existing public messages reader. The complete-loop
tests extend this same owner with the signed Worker/MetaBot processes, never a
replacement in-memory conversation service.
"""

import asyncio
import multiprocessing
import signal
import socket
import threading
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import psycopg
import uvicorn
from fastapi import FastAPI

from app.agent_brain.authorization import AgentUseAuthorization
from app.agent_brain.conversation_repository import ConversationRepository
from app.agent_brain.conversation_routes import (
    ConversationCursorCodec,
    build_conversation_router,
)
from app.agent_brain.direct_command_binding import DirectCommandBindingRepository
from app.agent_brain.repository import MissionRepository
from app.agent_brain.turn_snapshot import TurnSnapshotReader
from app.execution_relay.repository import ExecutionRelayRepository
from app.execution_relay.routes import build_execution_relay_router
from app.execution_relay.worker_auth import WorkerRequestVerifier


def _direct_process(database_url, context_factory=None, worker_factory=None):
    from app.agent_brain.conversation_context import ConversationContextBuilder
    from app.agent_brain.direct_mission_adapter import DirectMissionAdapter
    from app.agent_brain.direct_worker import DirectWorker
    from app.agent_brain.turn_attempts import TurnAttemptRepository
    from app.agent_brain.turn_result_projection import TurnResultProjector

    codec = _codec()
    attempts = TurnAttemptRepository(database_url, codec)
    bindings = DirectCommandBindingRepository(
        ExecutionRelayRepository(database_url, content_codec=codec)
    )
    repository = ConversationRepository(
        database_url,
        content_codec=codec,
        mission_repository=MissionRepository(database_url, content_codec=codec),
    )
    adapter = DirectMissionAdapter(
        attempts,
        bindings,
        context_factory(repository, database_url)
        if context_factory
        else ConversationContextBuilder(repository),
        TurnResultProjector(attempts, bindings),
    )
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    worker = (
        worker_factory(attempts, adapter, repository, database_url)
        if worker_factory
        else DirectWorker(attempts, adapter, lease_seconds=10)
    )
    worker.run(stopping)


def _machine_process(
    database_url,
    cloud_origin,
    worker_id,
    key_bytes,
    metabot_port,
    callback_port,
    secret_path,
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.execution_relay.metabot_client import (
        _APPROVED_AGENT_IDS,
        MetaBotClient,
        MetaBotRuntimeMap,
    )
    from app.execution_relay.worker import (
        SignedCloudClient,
        WorkerRuntime,
        callback_server,
    )
    from app.execution_relay.worker_auth import WorkerRequestSigner
    from app.execution_relay.worker_readiness_v5 import V5WorkerService
    from app.execution_relay.worker_store import WorkerStore

    async def run():
        cloud = SignedCloudClient(
            cloud_origin,
            WorkerRequestSigner(
                worker_id, "worker-v1", Ed25519PrivateKey.from_private_bytes(key_bytes)
            ),
        )
        ports = {name: 20000 + i for i, name in enumerate(sorted(_APPROVED_AGENT_IDS))}
        ports["hr-bot"] = metabot_port
        runtime = WorkerRuntime(
            worker_id=worker_id,
            cloud=cloud,
            store=WorkerStore(database_url),
            runtime_map=None,
            metabot=MetaBotClient(MetaBotRuntimeMap(ports), secret_path),
            callback_port=callback_port,
            enable_v5_callbacks=True,
        )
        signal.signal(signal.SIGTERM, lambda *_: runtime.stop())
        callback = asyncio.create_task(callback_server(runtime))
        await asyncio.wait_for(runtime.callback_ready.wait(), 5)
        service = V5WorkerService(runtime)
        service.start()
        try:
            await runtime.shutdown_event.wait()
        finally:
            await service.close()
            runtime.stop()
            await callback
            await cloud.aclose()

    asyncio.run(run())


from test_agent_brain_conversation_repository import _codec

from app.control_plane.auth import AuthSecrets, DingTalkWebAuth, WebSessionRepository
from app.control_plane.authorization import (
    AuthorizationRepository,
    AuthorizationService,
)
from app.control_plane.middleware import IdentitySecurityMiddleware


def _serve(database_url, listener, configure_app=None):
    secrets = AuthSecrets(b"w" * 32, key_version=1)

    async def disabled_login(_code):
        raise RuntimeError("External login is not available in the owned fixture")

    auth = DingTalkWebAuth(
        repository=WebSessionRepository(database_url, secrets=secrets),
        secrets=secrets,
        qr_login=disabled_login,
        in_client_login=disabled_login,
        environment="production",
        route_prefix="/",
        public_base_url="https://localhost",
        app_key="owned-web-fixture",
    )
    codec = _codec()
    repository = ConversationRepository(
        database_url,
        content_codec=codec,
        mission_repository=MissionRepository(database_url, content_codec=codec),
        worker_direct_enabled=True,
    )
    app = FastAPI()
    app.include_router(
        build_conversation_router(
            repository,
            AgentUseAuthorization(database_url),
            cursor_codec=ConversationCursorCodec(secrets),
            session_revalidator=auth.authenticate,
            session_cookie_name=auth.cookie_name,
            snapshot_reader=TurnSnapshotReader(repository),
        )
    )
    relay = ExecutionRelayRepository(database_url, content_codec=codec)
    app.include_router(
        build_execution_relay_router(
            relay,
            WorkerRequestVerifier(database_url),
            lease_seconds=60,
            max_body_bytes=1_048_576,
            v5_bindings=DirectCommandBindingRepository(relay),
        )
    )
    if configure_app is not None:
        configure_app(app, database_url)
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        authorization=AuthorizationService(AuthorizationRepository(database_url)),
        routes=tuple(app.router.routes),
    )
    uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False)).run(
        sockets=[listener]
    )


class WebLoop:
    configure_app = None
    context_factory = None
    worker_factory = None

    def __init__(self, environment, owner_id, conversation_id):
        self.machine = self.direct = None
        self.environment, self.owner_id, self.conversation_id = (
            environment,
            owner_id,
            conversation_id,
        )
        secrets = AuthSecrets(b"w" * 32, key_version=1)
        self.token, self.csrf = secrets.random_token(), secrets.random_token()
        generation, member = uuid4(), uuid4()
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "insert into platform_control.directory_generations(generation_id,status,member_count,source_member_count,department_count,source_schema_version,content_sha256,completed_at) values(%s,'complete',1,1,0,3,%s,now())",
                (generation, "a" * 64),
            )
            connection.execute(
                "update platform_control.internal_users set last_confirmed_generation_id=%s where internal_user_id=%s",
                (generation, owner_id),
            )
            connection.execute(
                "insert into platform_control.directory_members(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status,union_lookup_hmac,union_lookup_key_version) values(%s,%s,%s,'employee',%s,1,%s,1,'Owned fixture','active',%s,1)",
                (
                    generation,
                    member,
                    owner_id,
                    member.bytes * 2,
                    b"fixture",
                    generation.bytes * 2,
                ),
            )
            connection.execute(
                "update platform_control.directory_state set active_generation_id=%s,last_complete_at=now(),updated_at=now() where singleton",
                (generation,),
            )
            connection.execute(
                "insert into platform_control.web_sessions(session_id,internal_user_id,token_hash,token_hash_key_version,csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) values(%s,%s,%s,1,%s,1,now()+interval '1 hour',now()+interval '2 hours')",
                (
                    uuid4(),
                    owner_id,
                    secrets.digest("session", self.token),
                    secrets.digest("csrf", self.csrf),
                ),
            )
            connection.execute(
                "insert into platform_control.agent_use_grants(agent_use_grant_id,agent_id,target_kind,target_internal_user_id,created_by) values(%s,'hr-bot','user',%s,%s)",
                (uuid4(), owner_id, owner_id),
            )
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.origin = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        self.listener.listen()
        self.client = httpx.Client(
            base_url=self.origin,
            timeout=5,
            cookies={
                "__Host-platform_session": self.token,
                "__Host-platform_csrf": self.csrf,
            },
            headers={"Origin": "https://localhost", "X-CSRF-Token": self.csrf},
        )
        try:
            self.start_api()
        except BaseException:
            self.close()
            raise

    def start_api(self):
        self.process = multiprocessing.get_context("spawn").Process(
            target=_serve,
            args=(
                self.environment["urls"]["platform_control_app"],
                self.listener,
                self.configure_app,
            ),
        )
        self.process.start()
        deadline = monotonic() + 10
        while monotonic() < deadline:
            if not self.process.is_alive():
                raise AssertionError("Owned API process exited during setup")
            try:
                response = self.client.get(
                    f"/api/v1/conversations/{self.conversation_id}"
                )
                if response.status_code != 200:
                    raise AssertionError(
                        f"Owned authenticated API setup returned {response.status_code}"
                    )
                return
            except httpx.TransportError:
                sleep(0.05)
        raise AssertionError("Owned API startup deadline")

    def stop_api_process(self):
        self.process.kill()
        self.process.join(5)

    def restart_api_process(self):
        self.stop_api_process()
        self.start_api()

    def submit(self, text, request_id):
        response = self.client.post(
            f"/api/v1/conversations/{self.conversation_id}/messages",
            json={"text": text},
            headers={"Idempotency-Key": str(uuid5(NAMESPACE_URL, request_id))},
        )
        assert response.status_code in (200, 201), (
            response.status_code,
            response.json().get("detail"),
        )
        return response.json()["turn"]["turn_id"]

    def messages(self):
        response = self.client.get(
            f"/api/v1/conversations/{self.conversation_id}/messages"
        )
        assert response.status_code == 200
        return response.json()["items"]

    def snapshot(self, turn_id=None):
        response = self.client.get(
            f"/api/v1/conversations/{self.conversation_id}/snapshot",
            params={"turn_id": turn_id} if turn_id else {},
        )
        assert response.status_code == 200
        return response.json()

    def start_workers(self, worker_database, metabot_origin, callback_port, directory):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            PublicFormat,
        )
        from test_execution_worker_v5_receiver import MIGRATION

        with psycopg.connect(worker_database) as connection:
            connection.execute(
                "drop table if exists execution_worker.v5_callback_events,execution_worker.v5_callback_runs,execution_worker.v5_callback_metadata"
            )
            connection.execute(MIGRATION.read_text())
        self.worker_id = "web-loop-" + uuid4().hex
        key = Ed25519PrivateKey.generate()
        with psycopg.connect(self.environment["admin"]) as connection:
            connection.execute(
                "update platform_control.execution_workers set v5_observation=null"
            )
            connection.execute(
                "insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status) values(%s,array['hr-bot'],'active')",
                (self.worker_id,),
            )
            connection.execute(
                "insert into platform_control.execution_worker_keys(worker_id,key_id,public_key,status) values(%s,'worker-v1',%s,'active')",
                (
                    self.worker_id,
                    key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
                ),
            )
        directory.mkdir(mode=0o700)
        secret = directory / "machine"
        secret.write_text("fixture-web-secret")
        secret.chmod(0o600)
        context = multiprocessing.get_context("spawn")
        self.machine = context.Process(
            target=_machine_process,
            args=(
                worker_database,
                self.origin,
                self.worker_id,
                key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()),
                urlsplit(metabot_origin).port,
                callback_port,
                secret,
            ),
        )
        self.machine.start()
        self.start_direct()

    def start_direct(self):
        self.direct = multiprocessing.get_context("spawn").Process(
            target=_direct_process,
            args=(
                self.environment["urls"]["platform_control_app"],
                self.context_factory,
                self.worker_factory,
            ),
        )
        self.direct.start()

    def restart_direct(self):
        self.direct.kill()
        self.direct.join(5)
        self.start_direct()

    def wait_ready(self):
        def ready():
            with psycopg.connect(self.environment["admin"]) as connection:
                return connection.execute(
                    "select 1 from platform_control.execution_workers where worker_id=%s and v5_observation->>'ready'='true' and (v5_observation->>'expiresAt')::timestamptz>clock_timestamp()",
                    (self.worker_id,),
                ).fetchone()

        return wait_until(ready, timeout=20, description="signed live readiness")

    def wait_terminal(self, turn_id, status="completed", timeout=40):
        return wait_until(
            lambda: (
                value
                if (value := self.snapshot(turn_id))["attempt"]["status"] == status
                else None
            ),
            timeout=timeout,
            description=f"owned {status} terminal",
        )

    def cancel(self):
        response = self.client.post(
            f"/api/v1/conversations/{self.conversation_id}/turns/current/cancel"
        )
        assert response.status_code == 200

    def binding(self, turn_id):
        with psycopg.connect(
            self.environment["admin"], row_factory=psycopg.rows.dict_row
        ) as connection:
            return connection.execute(
                "select j.run_id,d.command_id,d.launch_lease_epoch,d.accepted_at,d.executor_stop_proof_ref,a.lease_epoch,a.executor_id,a.status from platform_control.turn_attempts a join platform_control.direct_command_bindings d using(attempt_id) join platform_control.execution_jobs j using(job_id) where a.turn_id=%s",
                (turn_id,),
            ).fetchone()

    def drive_until_available(self, turn_id, timeout=40):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            assert self.direct.is_alive() and self.machine.is_alive(), (
                "Owned worker process exited"
            )
            value = self.snapshot(turn_id)
            if value["answer"]:
                return value
            sleep(0.1)
        raise AssertionError(
            f"Answer deadline; turn={value['turn']['status']}, attempt={value['attempt']['status']}, reason={value['attempt']['reason_code']}"
        )

    def assistant_count(self, turn_id):
        with psycopg.connect(
            self.environment["urls"]["platform_control_app"]
        ) as connection:
            return connection.execute(
                "select count(*) from platform_control.conversation_messages where turn_id=%s and role='assistant'",
                (turn_id,),
            ).fetchone()[0]

    def close(self):
        for process in (self.direct, self.machine):
            if process is not None:
                process.terminate()
                process.join(10)
                if process.is_alive():
                    process.kill()
                    process.join(5)
        self.process.terminate()
        self.process.join(5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(5)
        self.client.close()
        self.listener.close()


def wait_until(predicate, *, timeout=40, description="owned condition"):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        value = predicate()
        if value:
            return value
        sleep(0.05)
    raise AssertionError(f"Deadline: {description}")
