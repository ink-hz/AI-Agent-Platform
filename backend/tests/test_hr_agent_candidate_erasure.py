"""Real uploads, candidate provenance, maintenance identity and object erasure."""

from uuid import UUID, uuid4

import pytest

from app.hr_agent.types import HrAgentProblem
from tests.test_hr_agent_candidate_intake import (
    batch_request,
    confirmation,
    finish_profile,
    intake,
)
from tests.test_hr_agent_materials import database, secured, uploaded

_FIXTURES = (database, secured, uploaded, intake)


def confirmed(uploaded, intake):
    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("review")
    item, _ = finish_profile(
        uploaded, intake, service.get_batch(owner, batch["batch_id"])["items"][0]
    )
    result = service.confirm_item(owner, item["item_id"], confirmation(item), uuid4())
    return item, result


def test_real_maintenance_erasure_after_candidate_confirmation(
    uploaded, intake, database
):
    from app.attachments.download_service import ConversationAttachmentAccessRepository
    from app.attachments.erasure import (
        AttachmentErasureRepository,
        AttachmentErasureService,
    )

    _, _, repo, owner, materials, aid, _, store = uploaded
    item, result = confirmed(uploaded, intake)
    service, resources = intake
    access = ConversationAttachmentAccessRepository(
        database.dsn, content_codec=repo.codec
    )
    access.request_erasure(owner, UUID(aid))
    eraser = AttachmentErasureService(
        AttachmentErasureRepository(
            database.dsn.replace(
                "user=platform_control_app", "user=platform_control_maintenance"
            ),
            content_codec=repo.codec,
        ),
        store,
    )
    assert eraser.process_next("independent-review")
    with database.connection() as c:
        assert c.execute(
            "SELECT state FROM platform_attachments.attachments WHERE attachment_id=%s",
            (aid,),
        ).fetchone() == ("deleted",)
        assert c.execute(
            "SELECT state FROM platform_attachments.erasure_jobs WHERE attachment_id=%s",
            (aid,),
        ).fetchone() == ("completed",)
    assert not store.objects
    with pytest.raises(HrAgentProblem):
        service.read_candidate(owner, result["candidate_id"])
    with pytest.raises(HrAgentProblem):
        service.results.read(owner, item["result_ref"])
    with pytest.raises(HrAgentProblem):
        materials.read_text(owner, item["text_ref"])
    with pytest.raises(HrAgentProblem):
        resources.validate_scope(
            owner, [{"kind": "candidate", "id": result["candidate_id"]}], []
        )
    assert service.get_item(owner, item["item_id"])["profile_body"] is None
    assert service.list_candidates(owner)["items"] == [
        {"candidate_id": result["candidate_id"], "available": False}
    ]


def test_one_claim_does_not_consume_sibling_erasure_jobs(uploaded, intake, database):
    from app.attachments.download_service import ConversationAttachmentAccessRepository
    from app.attachments.erasure import AttachmentErasureRepository
    from tests.test_hr_agent_material_parsing import upload_document

    _, _, repo, owner, _, aid, _, _ = uploaded
    second = upload_document(
        uploaded, database, b"Another synthetic source", "text/plain", "second.txt"
    )
    access = ConversationAttachmentAccessRepository(
        database.dsn, content_codec=repo.codec
    )
    for identity in (aid, second):
        access.request_erasure(owner, UUID(identity))
    repository = AttachmentErasureRepository(
        database.dsn.replace(
            "user=platform_control_app", "user=platform_control_maintenance"
        ),
        content_codec=repo.codec,
    )
    job = repository.claim("one-claim")
    assert job and str(job.attachment_id) == aid
    with database.connection() as c:
        assert c.execute(
            "SELECT state,count(*) FROM platform_attachments.erasure_jobs GROUP BY state ORDER BY state"
        ).fetchall() == [("queued", 1), ("running", 1)]


def test_erasure_migration_grants_only_needed_maintenance_columns(database):
    import psycopg

    maintenance = database.dsn.replace(
        "user=platform_control_app", "user=platform_control_maintenance"
    )
    with psycopg.connect(maintenance) as c:
        assert c.execute(
            "SELECT has_column_privilege(current_user,'platform_attachments.uploads','write_attempt_id','SELECT')"
        ).fetchone() == (True,)
        assert c.execute(
            "SELECT has_column_privilege(current_user,'platform_attachments.uploads','declared_mime','SELECT')"
        ).fetchone() == (False,)
        assert c.execute(
            "SELECT has_column_privilege(current_user,'platform_attachments.upload_write_attempts','object_ref_ciphertext','SELECT')"
        ).fetchone() == (True,)
        assert c.execute(
            "SELECT has_table_privilege(current_user,'platform_attachments.attachments','UPDATE')"
        ).fetchone() == (False,)
    with database.connection() as c:
        assert c.execute(
            "SELECT has_table_privilege(current_user,'platform_attachments.attachments','UPDATE')"
        ).fetchone() == (False,)
