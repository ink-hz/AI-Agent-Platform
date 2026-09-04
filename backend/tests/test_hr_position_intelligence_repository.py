from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
from test_control_plane_migration import control_database

from app.hr.models import CreateManualPosition
from app.hr.position_intelligence_models import (
    ConfirmContextModules,
    CreateContextDraft,
)
from app.hr.position_intelligence_repository import (
    PositionContextConflict,
    PositionContextNotFound,
    PositionIntelligenceRepository,
)
from app.hr.repository import HrPositionRepository


def _owner(connection, name: str):
    owner_id = uuid4()
    connection.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id,display_name,status) values (%s,%s,'active')",
        (owner_id, name),
    )
    connection.commit()
    return owner_id


@pytest.mark.postgres
def test_context_confirmation_is_replay_stable_and_rejects_a_stale_baseline(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Position Context Owner")
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "结构工程师"))
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    first_draft = repository.create_draft(CreateContextDraft(
        owner_id, uuid4(), position.position_id, None, None,
        {"mission": {"text": "Build reliable robots"}}, "Initial context", uuid4(),
    ))
    first_command = ConfirmContextModules(
        owner_id, position.position_id, first_draft.context_version_id, uuid4(),
        None, first_draft.row_version, ("mission",), owner_id,
    )

    first = repository.confirm_modules(first_command)
    assert repository.confirm_modules(first_command) == first
    second_draft = repository.create_draft(CreateContextDraft(
        owner_id, uuid4(), position.position_id, first.context_version_id, None,
        {"jd": {"duty": "Design structures"}}, "Second context", uuid4(),
    ))
    with pytest.raises(PositionContextConflict):
        repository.confirm_modules(ConfirmContextModules(
            owner_id, position.position_id, second_draft.context_version_id,
            uuid4(), None, second_draft.row_version, ("jd",), owner_id,
        ))

    current = repository.current(owner_id, position.position_id)
    assert current == first
    assert first.state == "confirmed"


@pytest.mark.postgres
def test_repository_keeps_partial_draft_and_compares_immutable_versions(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Partial Context Owner")
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "算法工程师"))
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    draft = repository.create_draft(CreateContextDraft(
        owner_id, uuid4(), position.position_id, None, None,
        {"mission": {"text": "Perception"}, "unknowns": {"items": ["team size"]}},
        "Proposed context", uuid4(),
    ))
    confirmed = repository.confirm_modules(ConfirmContextModules(
        owner_id, position.position_id, draft.context_version_id, uuid4(), None,
        draft.row_version, ("mission",), owner_id,
    ))

    drafts = repository.list_versions(owner_id, position.position_id, state="draft")
    assert drafts[0].modules == {"unknowns": {"items": ["team size"]}}
    assert drafts[0].row_version == 2
    comparison = repository.compare(
        owner_id, position.position_id, confirmed.context_version_id,
        drafts[0].context_version_id,
    )
    assert comparison["changed_modules"] == ("mission", "unknowns")
    assert comparison["left"]["mission"] == {"text": "Perception"}


@pytest.mark.postgres
def test_repository_conceals_cross_owner_contexts(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _owner(admin, "Visible Context Owner")
        other_id = _owner(admin, "Hidden Context Owner")
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "光学工程师"))
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    draft = repository.create_draft(CreateContextDraft(
        owner_id, uuid4(), position.position_id, None, None,
        {"jr": {"skills": ["optics"]}}, "JR", uuid4(),
    ))

    assert repository.current(other_id, position.position_id) is None
    assert repository.list_versions(other_id, position.position_id) == ()
    with pytest.raises(PositionContextNotFound):
        repository.compare(
            other_id, position.position_id, draft.context_version_id,
            draft.context_version_id,
        )
