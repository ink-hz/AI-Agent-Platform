from __future__ import annotations

from collections.abc import Sequence

from app.agent_brain.models import AgentCapabilityCard


ROSTER_HEADING = "# 已授权的专业 Agent"

ROSTER_EMPTY = f"""{ROSTER_HEADING}

当前用户没有任何已授权的专业 Agent。不要派发任务；如需说明原因，告知用户其账号
尚未获得专业 Agent 授权。
"""

ROSTER_UNAVAILABLE = f"""{ROSTER_HEADING}

授权服务当前不可用，本次无法提供清单。不要凭记忆或猜测描述任何专业 Agent；
需要清单时调用 list_agents，它失败时向用户说明能力清单暂不可读。
"""


def render_agent_roster(cards: Sequence[AgentCapabilityCard]) -> str:
    """Render an authorization-filtered roster as a byte-stable cached prompt block.

    The output must be identical for identical inputs: the block sits inside the
    Provider prompt-cache prefix, so any volatile value (timestamp, availability,
    latency sample) would defeat the cache on every Step. Live availability stays
    in list_agents.
    """

    if not isinstance(cards, Sequence) or isinstance(cards, (str, bytes)):
        raise ValueError("agent roster cards invalid")
    if any(not isinstance(card, AgentCapabilityCard) for card in cards):
        raise ValueError("agent roster cards invalid")
    if not cards:
        return ROSTER_EMPTY
    ordered = sorted(cards, key=lambda item: item.agent_id)
    sections = [ROSTER_HEADING, ""]
    sections.append("## 执行池")
    for pool, members in _pools(ordered):
        concurrency = members[0].pool_concurrency
        sections.append(
            f"- {pool}：同时最多 {concurrency} 个任务，成员 "
            f"{' / '.join(card.agent_id for card in members)}"
        )
    sections.append("")
    for card in ordered:
        sections.append(f"## {card.agent_id} · {card.display_name}（{card.domain_group}）")
        sections.append(f"- capability_version: {card.capability_version}")
        sections.append(
            f"- 执行池: {card.execution_pool}（并发 {card.pool_concurrency}）"
        )
        sections.append(f"- 职责: {card.mission}")
        sections.append(f"- 能力: {_join(card.capabilities)}")
        sections.append(f"- 不承担: {_join(card.exclusions)}")
        sections.append(f"- 必需输入: {_join(card.required_inputs)}")
        sections.append(f"- 任务示例: {_join(card.example_tasks)}")
        sections.append(
            f"- 时长: 典型 {card.typical_latency_seconds} 秒，"
            f"上限 {card.max_duration_seconds} 秒"
        )
        sections.append("")
    return "\n".join(sections)


def _pools(
    ordered: list[AgentCapabilityCard],
) -> list[tuple[str, list[AgentCapabilityCard]]]:
    grouped: dict[str, list[AgentCapabilityCard]] = {}
    for card in ordered:
        grouped.setdefault(card.execution_pool, []).append(card)
    return sorted(grouped.items())


def _join(values: Sequence[str]) -> str:
    return " / ".join(values)
