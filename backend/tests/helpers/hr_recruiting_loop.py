"""Only composition changes from WebLoop; no synthetic business state writer."""

import asyncio
import multiprocessing
import os
import signal
import threading
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg

from app.agent_brain.authorization import AgentUseAuthorization
from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.direct_worker import DirectWorker
from app.attachments.artifact_service import ArtifactOutputService, ArtifactRepository
from app.attachments.conversation_repository import ConversationAttachmentRepository
from app.attachments.conversation_routes import build_conversation_attachment_router
from app.attachments.download_service import (
    ConversationAttachmentAccessRepository,
    ConversationAttachmentDownloadService,
    S3ImmutableAttachmentStore,
)
from app.attachments.grant_service import AttachmentGrantService, TaskGrantRepository
from app.attachments.object_writer import AttachmentObjectWriter
from app.attachments.result_artifact_recovery import ArtifactRecovery
from app.attachments.scanner import TrustedInternalScanner
from app.attachments.upload_service import AttachmentUploadService
from app.attachments.validation import AttachmentValidator
from app.attachments.worker import AttachmentProcessor
from app.attachments.worker_runtime import AttachmentProcessingRepository
from app.hr.position_package_projection import (
    PositionPackageProjectionRepository,
    PositionPackageProjector,
)
from app.hr.repository import HrPositionRepository
from app.hr.routes import build_hr_position_router
from app.hr.service import HrPositionService
from app.hr.task_context import HrTaskContextProvider, PostgresHrTaskContextSource
from tests.helpers.hr_recruiting_objects import RecruitingObjects
from tests.helpers.hr_web_loop import WebLoop, _codec, wait_until

MODEL_VERSION = "claude-opus-4-8"


def configure_recruiting_api(app, database_url):
    objects = RecruitingObjects(Path(os.environ["HR_WEB_FIXTURE_ROOT"]) / "objects")
    codec = _codec()
    immutable = S3ImmutableAttachmentStore(objects, "owned-recruiting-files")
    writer = AttachmentObjectWriter(objects, "owned-recruiting-files")
    app.state.conversation_attachment_upload_service = AttachmentUploadService(
        ConversationAttachmentRepository(database_url, content_codec=codec), writer
    )
    app.state.conversation_attachment_download_service = (
        ConversationAttachmentDownloadService(
            ConversationAttachmentAccessRepository(database_url, content_codec=codec),
            immutable,
            ticket_secret=b"r" * 32,
        )
    )
    app.state.task_attachment_grant_service = AttachmentGrantService(
        TaskGrantRepository(database_url, content_codec=codec), immutable
    )
    app.state.artifact_output_service = ArtifactOutputService(
        ArtifactRepository(database_url, content_codec=codec), writer
    )
    app.include_router(build_conversation_attachment_router())
    app.include_router(
        build_hr_position_router(
            HrPositionService(HrPositionRepository(database_url)),
            AgentUseAuthorization(database_url),
        )
    )


def recruiting_context(repository, database_url):
    return ConversationContextBuilder(
        repository,
        hr_task_context_provider=HrTaskContextProvider(
            PostgresHrTaskContextSource(
                database_url, execution_model_version=MODEL_VERSION
            )
        ),
    )


def recruiting_worker(attempts, adapter, repository, database_url):
    adapter.attachment_grants = AttachmentGrantService(
        TaskGrantRepository(database_url, content_codec=repository.content_codec),
        None,
        grant_seconds=24 * 60 * 60,
    )
    return DirectWorker(
        attempts,
        adapter,
        lease_seconds=10,
        artifact_recovery=ArtifactRecovery(repository),
    )


def project_position_packages(database_url, brain_database_url):
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    projector = PositionPackageProjector(
        PositionPackageProjectionRepository(database_url),
        HrPositionService(HrPositionRepository(database_url)),
        _codec(),
        worker_id=f"recruiting-fixture-{uuid4().hex}",
        model_version=MODEL_VERSION,
    )
    processor = AttachmentProcessor(
        repository=AttachmentProcessingRepository(
            brain_database_url, content_codec=_codec()
        ),
        object_store=RecruitingObjects(
            Path(os.environ["HR_WEB_FIXTURE_ROOT"]) / "objects"
        ),
        validator=AttachmentValidator(),
        scanner=TrustedInternalScanner(),
        derivatives=None,
        worker_id=f"recruiting-files-{uuid4().hex}",
    )
    while not stopping.is_set():
        projector.reconcile_one()
        asyncio.run(processor.process_next())
        stopping.wait(0.25)


class RecruitingWebLoop(WebLoop):
    configure_app = staticmethod(configure_recruiting_api)
    context_factory = staticmethod(recruiting_context)
    worker_factory = staticmethod(recruiting_worker)

    def __init__(self, *args):
        self.projector = None
        (Path(os.environ["HR_WEB_FIXTURE_ROOT"]) / "objects").mkdir(mode=0o700)
        super().__init__(*args)
        self.projector = multiprocessing.get_context("spawn").Process(
            target=project_position_packages,
            args=(
                self.environment["urls"]["platform_control_app"],
                self.environment["urls"]["platform_brain_worker"],
            ),
        )
        self.projector.start()

    def position_ids(self):
        response = self.client.get("/api/hr/positions")
        assert response.status_code == 200, response.text
        return sorted(item["position_id"] for item in response.json()["items"])

    def upload_resume(self, name, content):
        response = self.client.post(
            "/api/v1/attachments/uploads",
            json={
                "conversation_id": str(self.conversation_id),
                "original_name": name,
                "declared_mime": "text/plain",
                "declared_size": len(content),
            },
        )
        assert response.status_code == 201, response.text
        upload = response.json()
        base = f"/api/v1/attachments/uploads/{upload['upload_id']}"
        assert self.client.put(base + "/content", content=content).status_code == 200
        assert self.client.post(base + "/complete").status_code == 200

        def ready():
            assert self.projector.is_alive(), "Owned attachment processor exited"
            value = self.client.get(
                f"/api/v1/attachments/{upload['attachment_id']}"
            ).json()
            if value["state"] in ("rejected", "failed"):
                with psycopg.connect(self.environment["admin"]) as connection:
                    reason = connection.execute(
                        "select state_reason from platform_attachments.attachments where attachment_id=%s",
                        (upload["attachment_id"],),
                    ).fetchone()
                raise AssertionError(
                    f"Real processor rejected synthetic input: {reason}"
                )
            return value if value["state"] == "ready" else None

        return wait_until(
            ready, timeout=15, description="real resume validation and scan"
        )

    def submit_with_resume(self, text, attachment_id):
        response = self.client.post(
            f"/api/v1/conversations/{self.conversation_id}/messages",
            json={
                "text": text,
                "attachment_ids": [attachment_id],
                "active_attachment_ids": [attachment_id],
            },
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 201, response.text
        return response.json()["turn"]["turn_id"]

    def download_artifact(self, attachment_id):
        route = f"/api/v1/attachments/{attachment_id}/ticket"
        denied = httpx.post(
            self.origin + route, json={"purpose": "download"}, timeout=5
        )
        assert denied.status_code == 401
        ticket = self.client.post(route, json={"purpose": "download"})
        assert ticket.status_code == 200, ticket.text
        content = self.client.get(ticket.json()["content_path"])
        assert content.status_code == 200
        assert content.headers["content-type"].startswith("application/pdf")
        return content.content

    def assert_one_output_charge(self, turn_id, attachment_id, size):
        with psycopg.connect(self.environment["admin"]) as connection:
            assert connection.execute(
                "select count(*) from platform_attachments.bindings where attachment_id=%s and kind='message_output'",
                (attachment_id,),
            ).fetchone() == (1,)
            assert connection.execute(
                "select count(*) from platform_attachments.uploads where attachment_id=%s",
                (attachment_id,),
            ).fetchone() == (1,)
            assert connection.execute(
                "select file_count,bytes_read from platform_attachments.task_grants grant_row join platform_control.mission_tasks task using(task_id) join platform_control.conversation_turns turn using(mission_id) where turn.turn_id=%s and grant_row.scope='write_output'",
                (turn_id,),
            ).fetchone() == (1, size)

    def close(self):
        if self.projector is not None:
            self.projector.terminate()
            self.projector.join(5)
            if self.projector.is_alive():
                self.projector.kill()
                self.projector.join(5)
        super().close()
        # Teardown only, after every owned writer has stopped. These immutable
        # synthetic HR records otherwise prevent the reused conversation fixture
        # from deleting its messages. Never used during business assertions.
        with psycopg.connect(self.environment["admin"]) as connection:
            connection.execute("set local session_replication_role=replica")
            for table in (
                "position_task_records",
                "position_task_requests",
                "position_package_projections",
                "position_draft_versions",
                "position_binding_events",
                "position_conversations",
                "position_context_versions",
                "position_drafts",
                "positions",
            ):
                connection.execute(
                    f"delete from platform_hr.{table} where owner_internal_user_id=%s",
                    (self.owner_id,),
                )
