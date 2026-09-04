from uuid import UUID

from app.hr.resource_backfill import (
    HistoricalConversationResources,
    HistoricalPositionBinding,
    apply_resource_bindings,
    discover_resource_bindings,
)


OWNER = UUID("00000000-0000-4000-8000-000000000001")
POSITION = UUID("00000000-0000-4000-8000-000000000002")
OTHER_POSITION = UUID("00000000-0000-4000-8000-000000000003")
CONVERSATION = UUID("00000000-0000-4000-8000-000000000004")
AMBIGUOUS_CONVERSATION = UUID("00000000-0000-4000-8000-000000000005")
MATERIAL = UUID("00000000-0000-4000-8000-000000000006")
AMBIGUOUS = UUID("00000000-0000-4000-8000-000000000007")
ARTIFACT = UUID("00000000-0000-4000-8000-000000000008")

CONVERSATIONS = (
    HistoricalConversationResources(CONVERSATION, OWNER, (MATERIAL,), (ARTIFACT,)),
    HistoricalConversationResources(AMBIGUOUS_CONVERSATION, OWNER, (AMBIGUOUS,), ()),
)
POSITION_BINDINGS = (
    HistoricalPositionBinding(CONVERSATION, OWNER, POSITION),
    HistoricalPositionBinding(AMBIGUOUS_CONVERSATION, OWNER, POSITION),
    HistoricalPositionBinding(AMBIGUOUS_CONVERSATION, OWNER, OTHER_POSITION),
)


def test_backfill_links_only_resources_from_exactly_bound_conversations():
    result = discover_resource_bindings(CONVERSATIONS, POSITION_BINDINGS)

    assert result.exact_material_ids == (MATERIAL,)
    assert result.ambiguous_attachment_ids == (AMBIGUOUS,)
    assert result.exact_artifact_ids == (ARTIFACT,)
    assert result.ambiguous_artifact_ids == ()
    assert result.exact_binding_count == 2


def test_backfill_apply_is_replay_safe_and_never_changes_turns():
    discovered = discover_resource_bindings(CONVERSATIONS, POSITION_BINDINGS)
    applied = []

    summary = apply_resource_bindings(
        discovered,
        lambda binding: applied.append((binding.resource_kind, binding.position_id, binding.resource_id, binding.request_id)),
    )
    replay = apply_resource_bindings(
        discovered,
        lambda binding: applied.append((binding.resource_kind, binding.position_id, binding.resource_id, binding.request_id)),
    )

    assert summary.applied_count == 2
    assert replay.applied_count == 2
    assert applied[0] == applied[2]
    assert {item[0] for item in applied} == {"material", "artifact"}
