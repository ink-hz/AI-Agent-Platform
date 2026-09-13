"""Local real identity/PG attachment canary; HR schema deliberately absent."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

PRODUCTION = Path(__file__).parents[2] / "artifacts/2026-09-13-hr-launch/production"


def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(PRODUCTION))
    path = PRODUCTION / "attachment_erasure_canary.py"
    assert path.is_file(), "independent attachment erasure canary missing"
    spec = importlib.util.spec_from_file_location("attachment_canary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(tmp_path):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.attachments.conversation_repository import ConversationAttachmentRepository
    from app.attachments.conversation_routes import build_conversation_attachment_router
    from app.attachments.upload_service import AttachmentUploadService
    from app.attachments.download_service import ConversationAttachmentAccessRepository, ConversationAttachmentDownloadService
    from app.attachments.derivatives import DerivativeBuilder
    from app.attachments.scanner import TrustedInternalScanner
    from app.attachments.validation import AttachmentValidator
    from app.attachments.worker import AttachmentProcessor
    from app.attachments.worker_runtime import AttachmentProcessingRepository
    from app.control_plane.auth import AuthSecrets, DingTalkWebAuth, WebSessionRepository
    from app.control_plane.authorization import AuthorizationRepository, AuthorizationService
    from app.control_plane.crypto import IdentityKeyring
    from app.control_plane.middleware import IdentitySecurityMiddleware
    from app.control_plane.routes_auth import build_auth_router
    from app.execution_relay.content_crypto import ContentCodec
    from tests.hr_agent_support import hr_agent_database
    from tests.test_hr_agent_materials import MemoryStore
    from tests.test_agent_brain_migration import _seed_active_directory

    with hr_agent_database(migrate_hr=False) as db:
        with db.admin_connection() as c:
            owner, *_ = _seed_active_directory(c)
            c.execute("update platform_control.internal_users set role='platform_owner',last_confirmed_generation_id=(select active_generation_id from platform_control.directory_state where singleton) where internal_user_id=%s", (owner,))
            assert c.execute("select to_regnamespace('platform_hr_agent')").fetchone()[0] is None
        async def login(_code, _verifier):
            return owner  # Only the external login exchange is local.
        secrets = AuthSecrets(b"e" * 32, key_version=1)
        auth = DingTalkWebAuth(repository=WebSessionRepository(db.dsn, secrets=secrets), secrets=secrets,
            qr_login=login, in_client_login=None, environment="production", route_prefix="/", public_base_url="https://localhost", app_key="local-erasure-canary")
        started = auth.start_qr("/")
        session = asyncio.run(auth.complete_qr(started.state, "local")).session
        codec = ContentCodec(IdentityKeyring(active_version=1, purpose="platform-content-encryption", _keys={1: b"e" * 32}))
        store = MemoryStore()
        app = FastAPI()
        app.state.conversation_attachment_upload_service = AttachmentUploadService(ConversationAttachmentRepository(db.dsn, content_codec=codec), store)
        app.state.conversation_attachment_download_service = ConversationAttachmentDownloadService(
            ConversationAttachmentAccessRepository(db.dsn, content_codec=codec), store, ticket_secret=b"d" * 32)
        app.include_router(build_conversation_attachment_router())
        app.include_router(build_auth_router(auth, static_dir=str(tmp_path), public_assets=frozenset(), detailed_health=dict))
        app.add_middleware(IdentitySecurityMiddleware, auth=auth, public_assets=frozenset(),
            authorization=AuthorizationService(AuthorizationRepository(db.dsn)), routes=tuple(app.router.routes))
        processor = AttachmentProcessor(repository=AttachmentProcessingRepository(db.dsn.replace("user=platform_control_app", "user=platform_brain_worker"), content_codec=codec),
            object_store=store, validator=AttachmentValidator(), scanner=TrustedInternalScanner(), derivatives=DerivativeBuilder(), worker_id="erasure-canary-engineering")
        config = dict(owner_id=str(owner), session_cookie=session.cookie_token, csrf=session.csrf_token,
                      public_origin="https://localhost", api_base_url="https://localhost")
        with TestClient(app, base_url="https://localhost") as client:
            class Scheduled:
                lose_delete = False
                def request(self, method, url, **kwargs):
                    result = client.request(method, url, **kwargs)
                    if method == "POST" and url.endswith("/complete") and result.status_code == 200:
                        for _ in range(8):
                            if not asyncio.run(processor.process_next()):
                                break
                    if method == "DELETE" and self.lose_delete:
                        self.lose_delete = False
                        import httpx
                        raise httpx.ReadTimeout("injected after actual DELETE acceptance")
                    return result
            yield dict(db=db, owner=owner, config=config, client=Scheduled(), store=store, codec=codec)


@pytest.mark.postgres
def test_prepare_erase_only_owned_attachment_and_http_absence_is_not_object_erasure(api, tmp_path, monkeypatch):
    h = harness(monkeypatch)
    runner = h.AttachmentCanary(api["config"], tmp_path / "run", api["client"])
    report = runner.prepare()
    assert report["status"] == "prepared"
    aid = report["attachment_id"]
    assert report["prepared"]["content_sha256"] == h.sha(runner.synthetic())
    objects = set(api["store"].objects)
    assert objects
    resumed = h.AttachmentCanary(api["config"], runner.directory, api["client"], resume=True)
    report = resumed.erase()
    assert report["status"] == "awaiting_external_erasure_evidence"
    assert report["attachment_id"] == aid
    assert report["operations"]["erase"]["response"] == {"http_status": 204}
    assert resumed.observe()["http_status"] == 404
    assert objects <= set(api["store"].objects), "HTTP disappearance precedes physical erasure"
    from app.attachments.erasure import AttachmentErasureRepository, AttachmentErasureService
    repository = AttachmentErasureRepository(api["db"].dsn.replace("user=platform_control_app", "user=platform_control_maintenance"), content_codec=api["codec"])
    service = AttachmentErasureService(repository, api["store"])
    assert service.process_next("owned-erasure-test")
    assert not (objects & set(api["store"].objects))
    with api["db"].admin_connection() as c:
        assert c.execute("select state from platform_attachments.erasure_jobs where attachment_id=%s", (aid,)).fetchone()[0] == "completed"
        assert c.execute("select count(*) from platform_attachments.attachments").fetchone()[0] == 1
    assert report["status"] == "awaiting_external_erasure_evidence", "script cannot certify object store"
    assert all("/hr/" not in row["path"] and "hr-readiness" not in row["path"] for row in report["http"])
    text = (runner.directory / "ledger.json").read_text()
    assert api["config"]["session_cookie"] not in text and api["config"]["csrf"] not in text


@pytest.mark.postgres
def test_expired_mutation_deadline_allows_only_bounded_observation(api, tmp_path, monkeypatch):
    h = harness(monkeypatch)
    runner = h.AttachmentCanary(api["config"], tmp_path / "expired", api["client"])
    runner.prepare()
    runner.ledger["deadline"] = 0
    runner.save()
    with pytest.raises(h.CanaryError, match="deadline_exhausted"):
        runner.erase()
    assert runner.observe()["state"] == "ready"
    assert runner.ledger["deadline"] == 0
    assert not any(row["method"] == "DELETE" for row in runner.ledger["http"])


@pytest.mark.postgres
def test_lost_delete_reply_stops_without_reissuing_delete(api, tmp_path, monkeypatch):
    h = harness(monkeypatch)
    runner = h.AttachmentCanary(api["config"], tmp_path / "unknown", api["client"])
    runner.prepare()
    api["client"].lose_delete = True
    with pytest.raises(h.CanaryError, match="outcome_unknown"):
        runner.erase()
    resumed = h.AttachmentCanary(api["config"], runner.directory, api["client"], resume=True)
    with pytest.raises(h.CanaryError, match="outcome_unknown"):
        resumed.erase()
    assert resumed.observe()["http_status"] == 404
    assert len([row for row in resumed.ledger["http"] if row["method"] == "DELETE"]) == 1
