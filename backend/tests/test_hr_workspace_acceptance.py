from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from app.agent_brain.conversation_routes import ConversationTextBody
from app.agent_catalog import AgentCatalogRepository


ROOT = Path(__file__).parents[2]


def test_hr_catalog_accepts_five_files_and_delivers_generated_files() -> None:
    card = AgentCatalogRepository().require("hr-bot")

    assert card.accepted_input_types == ("text", "image", "pdf", "office")
    assert card.output_types == ("text", "image", "pdf", "office")
    assert card.attachment_limits is not None
    assert card.attachment_limits.max_files_per_message == 5


def test_one_turn_can_select_two_old_materials_but_rejects_six_new_files() -> None:
    old = (uuid4(), uuid4())
    body = ConversationTextBody(
        text="只使用选中的两份材料",
        attachment_ids=(),
        active_attachment_ids=old,
    )
    assert body.active_attachment_ids == tuple(sorted(old, key=str))

    with pytest.raises(ValidationError):
        ConversationTextBody(
            text="too many",
            attachment_ids=tuple(uuid4() for _ in range(6)),
            active_attachment_ids=(),
        )


def test_cloud_enables_only_the_new_private_conversation_attachment_path() -> None:
    compose = yaml.safe_load(
        (ROOT / "deploy/cloud/compose.yaml").read_text(encoding="utf-8")
    )
    api = compose["services"]["platform-api"]["environment"]
    worker = compose["services"]["platform-attachments"]

    assert api["PLATFORM_ATTACHMENT_ENABLED"] == "0"
    assert api["PLATFORM_CONVERSATION_ATTACHMENT_ENABLED"] == "1"
    assert set(worker["networks"]) == {"platform-internal"}


def test_long_tasks_search_resume_and_unread_state_are_server_backed() -> None:
    routes = (ROOT / "backend/app/agent_brain/conversation_routes.py").read_text(
        encoding="utf-8"
    )
    migration = (
        ROOT / "backend/control_migrations/064_conversation_attachments.sql"
    ).read_text(encoding="utf-8")

    assert '"/api/v1/conversations/{conversation_id}/turns/{turn_id}/resume"' in routes
    assert "upsert_conversation_read_state_v64" in migration
    assert "last_read_message_seq" in migration


def test_result_versions_never_promote_a_failed_file_to_current() -> None:
    migration = (
        ROOT / "backend/control_migrations/064_conversation_attachments.sql"
    ).read_text(encoding="utf-8")

    assert "create view platform_attachments.current_artifact_versions" in migration
    assert "version.result_status = 'succeeded'" in migration
    assert "version.state = 'ready'" in migration


def test_deletion_and_expiry_revoke_access_and_retry_partial_object_failure() -> None:
    migration = (
        ROOT / "backend/control_migrations/064_conversation_attachments.sql"
    ).read_text(encoding="utf-8")

    assert "expire_task_grants_v64" in migration
    assert "state in ('queued','partial')" in migration
    assert "then now() + interval '5 minutes'" in migration
    assert "update platform_attachments.task_grants set revoked_at=now()" in migration


def test_release_gate_keeps_data_out_of_releases_and_staging_on_data_disk() -> None:
    stage = (ROOT / "deploy/cloud/remote-stage.sh").read_text(encoding="utf-8")

    assert 'staging_path="/data/staging/orbbec-agent-platform"' in stage
    assert "current + two rollback" in stage
    assert "archive retention: ten releases or thirty days" in stage
    assert '"$release_path/PREVIOUS_PLATFORM_ENV"' not in stage
