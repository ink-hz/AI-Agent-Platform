from __future__ import annotations

import pytest

from app.agent_brain.agent_roster import (
    ROSTER_EMPTY,
    ROSTER_UNAVAILABLE,
    render_agent_roster,
)
from app.agent_brain.models import load_capability_cards


def test_roster_lists_every_delegatable_agent_with_its_capability_version() -> None:
    cards = load_capability_cards()

    rendered = render_agent_roster(cards)

    for card in cards:
        assert f"## {card.agent_id} · {card.display_name}" in rendered
        assert f"capability_version: {card.capability_version}" in rendered
        assert card.mission in rendered
    # The Brain answers "which Agents exist" from this block alone; a missing Agent
    # would silently disappear from its view.
    assert rendered.count("capability_version:") == len(cards)


def test_roster_is_byte_stable_regardless_of_input_order() -> None:
    cards = load_capability_cards()

    assert render_agent_roster(cards) == render_agent_roster(tuple(reversed(cards)))


def test_roster_carries_no_volatile_value() -> None:
    rendered = render_agent_roster(load_capability_cards())

    # The block sits inside the Provider cache prefix, so anything that changes
    # between Steps would defeat the cache on every Step.
    for volatile in ("availability", "healthy", "offline", "unknown", "sampled"):
        assert volatile not in rendered


def test_roster_states_the_absence_of_authorized_agents() -> None:
    assert render_agent_roster(()) == ROSTER_EMPTY
    assert "没有任何已授权" in ROSTER_EMPTY
    # The two degraded blocks must not read alike: one is a real empty grant set, the
    # other is an unreadable authorization service.
    assert ROSTER_EMPTY != ROSTER_UNAVAILABLE
    assert "list_agents" in ROSTER_UNAVAILABLE


def test_roster_rejects_values_that_are_not_capability_cards() -> None:
    with pytest.raises(ValueError):
        render_agent_roster("hr-bot")
    with pytest.raises(ValueError):
        render_agent_roster(({"agent_id": "hr-bot"},))


def test_roster_groups_agents_by_the_executor_they_share() -> None:
    rendered = render_agent_roster(load_capability_cards())

    # The pool section states the constraint once, up front: six Agents, one slot.
    assert "## 执行池" in rendered
    assert "metabot_local：同时最多 1 个任务" in rendered
    assert "hr-bot / marketing-gtm-bot" in rendered
    # And each Agent repeats it, so a delegation decision cannot miss it.
    assert rendered.count("- 执行池: metabot_local（并发 1）") == 6
