"""Deterministic, audit-friendly discovery for historical HR resources."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid5

_BACKFILL_NAMESPACE = UUID("d0562681-7d4a-4cf0-9e11-f2aab1e227cb")


def _uuid(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("historical resource identifier invalid")
    return value


def _ids(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
    if not isinstance(values, tuple) or any(not isinstance(value, UUID) for value in values):
        raise ValueError("historical resource identifiers invalid")
    return tuple(sorted(set(values), key=str))


@dataclass(frozen=True, slots=True)
class HistoricalConversationResources:
    conversation_id: UUID
    owner_id: UUID
    attachment_ids: tuple[UUID, ...] = ()
    artifact_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        _uuid(self.conversation_id)
        _uuid(self.owner_id)
        object.__setattr__(self, "attachment_ids", _ids(self.attachment_ids))
        object.__setattr__(self, "artifact_ids", _ids(self.artifact_ids))


@dataclass(frozen=True, slots=True)
class HistoricalPositionBinding:
    conversation_id: UUID
    owner_id: UUID
    position_id: UUID

    def __post_init__(self) -> None:
        _uuid(self.conversation_id)
        _uuid(self.owner_id)
        _uuid(self.position_id)


@dataclass(frozen=True, slots=True)
class ResourceBinding:
    owner_id: UUID
    position_id: UUID
    resource_id: UUID
    resource_kind: str
    request_id: UUID

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.position_id, self.resource_id, self.request_id):
            _uuid(value)
        if self.resource_kind not in {"material", "artifact"}:
            raise ValueError("historical resource kind invalid")


@dataclass(frozen=True, slots=True)
class ResourceBindingDiscovery:
    bindings: tuple[ResourceBinding, ...]
    ambiguous_attachment_ids: tuple[UUID, ...]
    ambiguous_artifact_ids: tuple[UUID, ...]

    @property
    def exact_material_ids(self) -> tuple[UUID, ...]:
        return tuple(value.resource_id for value in self.bindings if value.resource_kind == "material")

    @property
    def exact_artifact_ids(self) -> tuple[UUID, ...]:
        return tuple(value.resource_id for value in self.bindings if value.resource_kind == "artifact")

    @property
    def exact_binding_count(self) -> int:
        return len(self.bindings)

    def counts(self) -> dict[str, int]:
        return {
            "exact_materials": len(self.exact_material_ids),
            "exact_artifacts": len(self.exact_artifact_ids),
            "ambiguous_attachments": len(self.ambiguous_attachment_ids),
            "ambiguous_artifacts": len(self.ambiguous_artifact_ids),
        }


@dataclass(frozen=True, slots=True)
class AppliedResourceBindings:
    applied_count: int
    noop_count: int
    material_count: int
    artifact_count: int
    ambiguous_attachment_count: int
    ambiguous_artifact_count: int


class HistoricalResourceRepository(Protocol):
    def conversation_resources(
        self, owner_id: UUID, conversation_ids: tuple[UUID, ...]
    ) -> tuple[HistoricalConversationResources, ...]: ...

    def position_bindings_for_conversations(
        self, owner_id: UUID, conversation_ids: tuple[UUID, ...]
    ) -> tuple[HistoricalPositionBinding, ...]: ...

    def apply_resource_binding(self, binding: ResourceBinding) -> bool: ...


def _binding(owner_id: UUID, position_id: UUID, resource_id: UUID, resource_kind: str) -> ResourceBinding:
    request_id = uuid5(_BACKFILL_NAMESPACE, f"{owner_id}:{position_id}:{resource_kind}:{resource_id}")
    return ResourceBinding(owner_id, position_id, resource_id, resource_kind, request_id)


def discover_resource_bindings(
    conversations: Iterable[HistoricalConversationResources],
    position_bindings: Iterable[HistoricalPositionBinding],
) -> ResourceBindingDiscovery:
    """Discover only single-position resources; report all other source ids as ambiguous."""
    positions: dict[tuple[UUID, UUID], set[UUID]] = defaultdict(set)
    for binding in position_bindings:
        if not isinstance(binding, HistoricalPositionBinding):
            raise ValueError("historical position binding required")
        positions[(binding.owner_id, binding.conversation_id)].add(binding.position_id)
    exact: list[ResourceBinding] = []
    ambiguous_attachments: set[UUID] = set()
    ambiguous_artifacts: set[UUID] = set()
    for conversation in conversations:
        if not isinstance(conversation, HistoricalConversationResources):
            raise ValueError("historical conversation resources required")
        bound = positions[(conversation.owner_id, conversation.conversation_id)]
        if len(bound) != 1:
            ambiguous_attachments.update(conversation.attachment_ids)
            ambiguous_artifacts.update(conversation.artifact_ids)
            continue
        position_id = next(iter(bound))
        exact.extend(_binding(conversation.owner_id, position_id, resource_id, "material") for resource_id in conversation.attachment_ids)
        exact.extend(_binding(conversation.owner_id, position_id, resource_id, "artifact") for resource_id in conversation.artifact_ids)
    exact.sort(key=lambda value: (str(value.owner_id), str(value.position_id), value.resource_kind, str(value.resource_id)))
    return ResourceBindingDiscovery(
        tuple(exact), tuple(sorted(ambiguous_attachments, key=str)), tuple(sorted(ambiguous_artifacts, key=str)),
    )


def apply_resource_bindings(
    discovery: ResourceBindingDiscovery,
    apply: Callable[[ResourceBinding], bool],
) -> AppliedResourceBindings:
    """Apply a discovery using deterministic request ids; callers can safely replay it."""
    if not isinstance(discovery, ResourceBindingDiscovery) or not callable(apply):
        raise ValueError("resource binding application invalid")
    applied: list[ResourceBinding] = []
    noop_count = 0
    for binding in discovery.bindings:
        result = apply(binding)
        if type(result) is not bool:
            raise ValueError("resource binding result invalid")
        if result:
            applied.append(binding)
        else:
            noop_count += 1
    return AppliedResourceBindings(
        len(applied), noop_count,
        sum(value.resource_kind == "material" for value in applied),
        sum(value.resource_kind == "artifact" for value in applied),
        len(discovery.ambiguous_attachment_ids), len(discovery.ambiguous_artifact_ids),
    )
