# ruff: noqa: TRY004
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid5

from .panorama_models import (
    PositionInsightRetrieval,
    TalentInsightVersion,
    TalentSource,
    canonical_panorama_url,
    thaw_json,
)
from .panorama_repository import (
    PanoramaConflict,
    PanoramaRepositoryError,
)

MAX_PANORAMA_CONTEXT_BYTES = 32 * 1024
MAX_PANORAMA_INSIGHTS = 5
DEFAULT_STALE_AFTER = timedelta(days=30)
_EXPLICIT_TRIGGERS = (
    "竞品",
    "招聘情报",
    "全景分析",
    "外部岗位",
    "参考关注公司",
)
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")

_USAGE_BOUNDARY = {
    "facts": "may_be_cited_with_https_source",
    "inferences": "must_be_explicitly_labelled_as_ai_inference",
    "unknowns": "must_remain_unknown_not_negative_fact",
    "position_changes": "draft_only_until_user_confirmation",
    "automatic_position_write": "forbidden",
}


class PanoramaContextError(RuntimeError):
    pass


class PanoramaContextSource(Protocol):
    def list_sources_page(
        self,
        owner_id: UUID,
        *,
        include_inactive: bool = False,
        before_created_at: datetime | None = None,
        before_source_id: UUID | None = None,
        limit: int = 100,
    ) -> tuple[TalentSource, ...]: ...

    def relevant_insights(
        self, owner_id: UUID, query: str, position_id: UUID, *, limit: int = 5
    ) -> tuple[TalentInsightVersion, ...]: ...

    def retrieval_for_turn(
        self, owner_id: UUID, position_id: UUID, turn_id: UUID
    ) -> PositionInsightRetrieval | None: ...

    def record_retrieval_for_turn(
        self,
        *,
        retrieval_id: UUID,
        owner_id: UUID,
        client_request_id: UUID,
        position_id: UUID,
        turn_id: UUID,
        insight_version_ids: tuple[UUID, ...],
        query_sha256: str,
        retrieved_excerpts: tuple[Mapping[str, object], ...],
    ) -> PositionInsightRetrieval: ...


def _encoded_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _postgres_jsonb_text_size(value: object) -> int:
    return len(
        json.dumps(
            thaw_json(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(", ", ": "),
            allow_nan=False,
        ).encode("utf-8")
    )


def _bounded_text(value: object, maximum_bytes: int) -> tuple[str, bool]:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise PanoramaContextError("panorama excerpt invalid")
    normalized = value.strip()
    encoded = normalized.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return normalized, False
    suffix = "…"
    budget = maximum_bytes - len(suffix.encode("utf-8"))
    clipped = encoded[:budget]
    while True:
        try:
            return clipped.decode("utf-8") + suffix, True
        except UnicodeDecodeError:
            clipped = clipped[:-1]


def _query_hash(query: str) -> str:
    if not isinstance(query, str):
        raise ValueError("panorama query invalid")
    encoded = query.encode("utf-8")
    if not query.strip() or "\0" in query or len(encoded) > 32768:
        raise ValueError("panorama query invalid")
    return hashlib.sha256(encoded).hexdigest()


def _normalized(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def _mentions_name(query: str, name: str) -> bool:
    folded_query = unicodedata.normalize("NFKC", query).casefold()
    folded_name = unicodedata.normalize("NFKC", name).strip().casefold()
    if not folded_name:
        return False
    if folded_name.isascii() and all(
        character.isalnum() or character in " ._-" for character in folded_name
    ):
        words = [re.escape(word) for word in re.split(r"[\s._-]+", folded_name) if word]
        if not words:
            return False
        pattern = r"(?<![a-z0-9])" + r"[\s._-]+".join(words) + r"(?![a-z0-9])"
        return re.search(pattern, folded_query) is not None
    name_key = _normalized(folded_name)
    return len(name_key) >= 2 and name_key in _normalized(folded_query)


def _has_explicit_trigger(query: str) -> bool:
    query_key = _normalized(query)
    return any(_normalized(trigger) in query_key for trigger in _EXPLICIT_TRIGGERS)


def _mentions_source(query: str, sources: tuple[TalentSource, ...]) -> bool:
    return any(
        _mentions_name(query, name)
        for source in sources
        if source.active
        for name in (source.canonical_name, *source.aliases)
    )


def _observed_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise PanoramaContextError("panorama fact timestamp invalid")
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PanoramaContextError("panorama fact timestamp invalid") from None
    if observed.tzinfo is None:
        raise PanoramaContextError("panorama fact timestamp invalid")
    return observed


def _latest_per_scope(
    insights: tuple[TalentInsightVersion, ...], owner_id: UUID
) -> tuple[TalentInsightVersion, ...]:
    if len(insights) > MAX_PANORAMA_INSIGHTS:
        raise PanoramaContextError("panorama insight selection invalid")
    groups: dict[tuple[str, ...], tuple[int, TalentInsightVersion]] = {}
    for index, insight in enumerate(insights):
        if (
            not isinstance(insight, TalentInsightVersion)
            or insight.owner_id != owner_id
        ):
            raise PanoramaContextError("panorama insight scope invalid")
        key = tuple(sorted(str(value) for value in insight.selected_source_ids))
        previous = groups.get(key)
        if previous is None or insight.created_at > previous[1].created_at:
            groups[key] = (index if previous is None else previous[0], insight)
    return tuple(item[1] for item in sorted(groups.values(), key=lambda item: item[0]))


@dataclass(frozen=True, slots=True)
class PanoramaContextFragment:
    insight_version_ids: tuple[UUID, ...]
    query_sha256: str
    as_of: datetime
    facts: tuple[Mapping[str, object], ...]
    inferences: tuple[Mapping[str, object], ...]
    unknowns: tuple[Mapping[str, object], ...]
    source_urls: tuple[str, ...]
    stale_age_days: int | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.insight_version_ids, tuple)
            or not 1 <= len(self.insight_version_ids) <= MAX_PANORAMA_INSIGHTS
            or any(not isinstance(value, UUID) for value in self.insight_version_ids)
            or len(set(self.insight_version_ids)) != len(self.insight_version_ids)
        ):
            raise ValueError("panorama context insight IDs invalid")
        if (
            not isinstance(self.query_sha256, str)
            or _SHA256.fullmatch(self.query_sha256) is None
        ):
            raise ValueError("panorama context query hash invalid")
        if not isinstance(self.as_of, datetime) or self.as_of.tzinfo is None:
            raise ValueError("panorama context as-of invalid")
        for values in (self.facts, self.inferences, self.unknowns):
            if not isinstance(values, tuple) or any(
                not isinstance(value, Mapping) for value in values
            ):
                raise ValueError("panorama context excerpts invalid")
        normalized_urls = tuple(
            canonical_panorama_url(value) for value in self.source_urls
        )
        if len(set(normalized_urls)) != len(normalized_urls):
            raise ValueError("panorama context citations invalid")
        object.__setattr__(self, "source_urls", normalized_urls)
        if self.stale_age_days is not None and (
            isinstance(self.stale_age_days, bool)
            or not isinstance(self.stale_age_days, int)
            or self.stale_age_days < 0
        ):
            raise ValueError("panorama context age invalid")
        insight_ids = {str(value) for value in self.insight_version_ids}
        fact_keys = {
            (str(item.get("insight_version_id")), str(item.get("fact_id")))
            for item in self.facts
        }
        fact_sources: dict[tuple[str, str], tuple[str, str]] = {}
        retained_urls: list[str] = []
        for item in self.facts:
            if set(item) != {
                "insight_version_id",
                "fact_id",
                "text",
                "source_url",
                "observed_at",
                "truncated",
            }:
                raise ValueError("panorama context fact schema invalid")
            insight_id = str(item.get("insight_version_id"))
            fact_id = item.get("fact_id")
            text = item.get("text")
            truncated = item.get("truncated")
            source_url = item.get("source_url")
            observed_at = item.get("observed_at")
            if (
                insight_id not in insight_ids
                or not isinstance(fact_id, str)
                or not fact_id
                or not isinstance(text, str)
                or not text
                or type(truncated) is not bool
                or not isinstance(source_url, str)
                or canonical_panorama_url(source_url) != source_url
            ):
                raise ValueError("panorama context fact scope invalid")
            _observed_at(observed_at)
            fact_sources[(insight_id, fact_id)] = (source_url, str(observed_at))
            if source_url not in retained_urls:
                retained_urls.append(source_url)
        if tuple(retained_urls) != self.source_urls:
            raise ValueError("panorama context fact citations invalid")
        for item in self.inferences:
            if set(item) != {
                "insight_version_id",
                "text",
                "basis_fact_ids",
                "basis_sources",
                "truncated",
            }:
                raise ValueError("panorama context inference schema invalid")
            insight_id = str(item.get("insight_version_id"))
            basis = item.get("basis_fact_ids")
            basis_sources = item.get("basis_sources")
            if (
                insight_id not in insight_ids
                or not isinstance(item.get("text"), str)
                or not item.get("text")
                or type(item.get("truncated")) is not bool
                or not isinstance(basis, (tuple, list))
                or not basis
                or any((insight_id, str(fact_id)) not in fact_keys for fact_id in basis)
                or not isinstance(basis_sources, (tuple, list))
            ):
                raise ValueError("panorama context inference boundary invalid")
            expected_sources = [
                {
                    "source_url": fact_sources[(insight_id, str(fact_id))][0],
                    "observed_at": fact_sources[(insight_id, str(fact_id))][1],
                }
                for fact_id in basis
            ]
            if list(basis_sources) != expected_sources:
                raise ValueError("panorama context inference provenance invalid")
        for item in self.unknowns:
            if (
                set(item)
                != {
                    "insight_version_id",
                    "text",
                    "source_urls",
                    "evidence_status",
                    "as_of",
                    "truncated",
                }
                or str(item.get("insight_version_id")) not in insight_ids
                or not isinstance(item.get("text"), str)
                or not item.get("text")
                or item.get("source_urls") not in ((), [])
                or item.get("evidence_status") != "unverified"
                or type(item.get("truncated")) is not bool
            ):
                raise ValueError("panorama context unknown scope invalid")
            _observed_at(item.get("as_of"))
        if _encoded_size(self.as_prompt_document()) > MAX_PANORAMA_CONTEXT_BYTES:
            raise ValueError("panorama context exceeds limit")

    def as_prompt_document(self) -> dict[str, object]:
        freshness: dict[str, object] = {
            "as_of": self.as_of.isoformat(),
            "status": "current",
        }
        if self.stale_age_days is not None:
            freshness.update(
                {
                    "status": "stale_last_valid",
                    "age_days": self.stale_age_days,
                    "warning": (
                        "This fragment contains stale Panorama evidence; the oldest "
                        f"included evidence is {self.stale_age_days} days old. Cite "
                        "its age and do not "
                        "infer that missing current data means hiring stopped."
                    ),
                }
            )
        return {
            "insight_version_ids": [str(value) for value in self.insight_version_ids],
            "query_sha256": self.query_sha256,
            "freshness": freshness,
            "facts": [thaw_json(value) for value in self.facts],
            "inferences": [thaw_json(value) for value in self.inferences],
            "unknowns": [thaw_json(value) for value in self.unknowns],
            "source_urls": list(self.source_urls),
            "usage_boundary": dict(_USAGE_BOUNDARY),
        }

    @classmethod
    def from_prompt_document(cls, value: object) -> PanoramaContextFragment:
        if not isinstance(value, Mapping) or set(value) != {
            "insight_version_ids",
            "query_sha256",
            "freshness",
            "facts",
            "inferences",
            "unknowns",
            "source_urls",
            "usage_boundary",
        }:
            raise PanoramaContextError("recorded panorama context invalid")
        if thaw_json(value.get("usage_boundary")) != _USAGE_BOUNDARY:
            raise PanoramaContextError("recorded panorama context invalid")
        freshness = value.get("freshness")
        if not isinstance(freshness, Mapping):
            raise PanoramaContextError("recorded panorama context invalid")
        try:
            status = freshness["status"]
            stale_age_days = (
                int(freshness["age_days"]) if status == "stale_last_valid" else None
            )
            if status not in {"current", "stale_last_valid"}:
                raise ValueError
            if status == "stale_last_valid" and not isinstance(
                freshness.get("warning"), str
            ):
                raise ValueError
            document_ids = tuple(
                UUID(str(item)) for item in value["insight_version_ids"]
            )
            fragment = cls(
                insight_version_ids=document_ids,
                query_sha256=str(value["query_sha256"]),
                as_of=_observed_at(freshness["as_of"]),
                facts=tuple(dict(item) for item in value["facts"]),
                inferences=tuple(dict(item) for item in value["inferences"]),
                unknowns=tuple(dict(item) for item in value["unknowns"]),
                source_urls=tuple(value["source_urls"]),
                stale_age_days=stale_age_days,
            )
        except (KeyError, TypeError, ValueError):
            raise PanoramaContextError("recorded panorama context invalid") from None
        if fragment.as_prompt_document() != thaw_json(value):
            raise PanoramaContextError("recorded panorama context invalid")
        return fragment


class PanoramaContextProvider:
    def __init__(
        self,
        source: PanoramaContextSource,
        *,
        now=None,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        for method in (
            "list_sources_page",
            "relevant_insights",
            "retrieval_for_turn",
            "record_retrieval_for_turn",
        ):
            if not callable(getattr(source, method, None)):
                raise ValueError("panorama context source invalid")
        if now is not None and not callable(now):
            raise ValueError("panorama context clock invalid")
        if not isinstance(stale_after, timedelta) or stale_after <= timedelta(0):
            raise ValueError("panorama stale duration invalid")
        self._source = source
        self._now = now or (lambda: datetime.now().astimezone())
        self._stale_after = stale_after

    def for_turn(
        self, owner_id: UUID, position_id: UUID, query: str, turn_id: UUID
    ) -> PanoramaContextFragment | None:
        if any(
            not isinstance(value, UUID) for value in (owner_id, position_id, turn_id)
        ):
            raise ValueError("panorama context identifiers invalid")
        query_sha256 = _query_hash(query)
        try:
            existing = self._source.retrieval_for_turn(owner_id, position_id, turn_id)
            if existing is not None:
                return self._replay(
                    existing, query_sha256, owner_id, position_id, turn_id
                )
            if not self._query_triggers_retrieval(owner_id, query):
                return None
            insights = _latest_per_scope(
                self._source.relevant_insights(
                    owner_id, query, position_id, limit=MAX_PANORAMA_INSIGHTS
                ),
                owner_id,
            )
            if not insights:
                return None
            fragment = self._compose(insights, query_sha256)
            try:
                recorded = self._source.record_retrieval_for_turn(
                    retrieval_id=uuid5(turn_id, "hr-panorama-context-v1"),
                    owner_id=owner_id,
                    client_request_id=turn_id,
                    position_id=position_id,
                    turn_id=turn_id,
                    insight_version_ids=fragment.insight_version_ids,
                    query_sha256=query_sha256,
                    retrieved_excerpts=(fragment.as_prompt_document(),),
                )
            except PanoramaConflict:
                recorded = self._source.retrieval_for_turn(
                    owner_id, position_id, turn_id
                )
                if recorded is None:
                    raise
            return self._replay(recorded, query_sha256, owner_id, position_id, turn_id)
        except PanoramaConflict:
            raise
        except (PanoramaRepositoryError, ValueError, TypeError, UnicodeError):
            raise PanoramaContextError("panorama context unavailable") from None

    def _query_triggers_retrieval(self, owner_id: UUID, query: str) -> bool:
        if _has_explicit_trigger(query):
            return True
        before_created_at = None
        before_source_id = None
        while True:
            sources = self._source.list_sources_page(
                owner_id,
                include_inactive=False,
                before_created_at=before_created_at,
                before_source_id=before_source_id,
                limit=100,
            )
            if not isinstance(sources, tuple) or any(
                not isinstance(source, TalentSource)
                or source.owner_id != owner_id
                or not source.active
                for source in sources
            ):
                raise PanoramaContextError("panorama source scope invalid")
            if _mentions_source(query, sources):
                return True
            if len(sources) < 100:
                return False
            cursor = (sources[-1].created_at, sources[-1].source_id)
            if cursor == (before_created_at, before_source_id):
                raise PanoramaContextError("panorama source page invalid")
            before_created_at, before_source_id = cursor

    @staticmethod
    def _replay(
        retrieval: PositionInsightRetrieval,
        query_sha256: str,
        owner_id: UUID,
        position_id: UUID,
        turn_id: UUID,
    ) -> PanoramaContextFragment:
        if (
            not isinstance(retrieval, PositionInsightRetrieval)
            or retrieval.owner_id != owner_id
            or retrieval.position_id != position_id
            or retrieval.turn_id != turn_id
        ):
            raise PanoramaContextError("recorded panorama context scope invalid")
        if retrieval.query_sha256 != query_sha256:
            raise PanoramaConflict("panorama turn query conflict")
        if len(retrieval.retrieved_excerpts) != 1:
            raise PanoramaContextError("recorded panorama context invalid")
        fragment = PanoramaContextFragment.from_prompt_document(
            retrieval.retrieved_excerpts[0]
        )
        if (
            fragment.insight_version_ids != retrieval.insight_version_ids
            or fragment.query_sha256 != retrieval.query_sha256
        ):
            raise PanoramaContextError("recorded panorama context invalid")
        return fragment

    def _compose(
        self, insights: tuple[TalentInsightVersion, ...], query_sha256: str
    ) -> PanoramaContextFragment:
        insight_ids = tuple(insight.insight_version_id for insight in insights)
        available_observed = tuple(
            _observed_at(fact["observed_at"])
            for insight in insights
            for fact in insight.facts
        )
        if not available_observed:
            raise PanoramaContextError("panorama facts unavailable")
        provisional_as_of = max(available_observed)
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise PanoramaContextError("panorama context clock invalid")
        facts: list[dict[str, object]] = []
        source_urls: list[str] = []
        for insight in insights:
            for fact in insight.facts:
                text, truncated = _bounded_text(fact.get("text"), 2000)
                source_url = canonical_panorama_url(fact.get("source_url"))
                candidate = {
                    "insight_version_id": str(insight.insight_version_id),
                    "fact_id": str(fact.get("fact_id")),
                    "text": text,
                    "source_url": source_url,
                    "observed_at": _observed_at(fact.get("observed_at")).isoformat(),
                    "truncated": truncated,
                }
                candidate_urls = [*source_urls]
                if source_url not in candidate_urls:
                    candidate_urls.append(source_url)
                if self._fits(
                    insight_ids,
                    query_sha256,
                    provisional_as_of,
                    None,
                    [*facts, candidate],
                    [],
                    [],
                    candidate_urls,
                ):
                    facts.append(candidate)
                    source_urls = candidate_urls
        if not facts:
            raise PanoramaContextError("panorama facts unavailable")
        while True:
            retained_observed = tuple(
                _observed_at(fact["observed_at"]) for fact in facts
            )
            as_of = max(retained_observed)
            oldest_age = max(now - min(retained_observed), timedelta(0))
            stale_age_days = oldest_age.days if oldest_age > self._stale_after else None
            source_urls = list(dict.fromkeys(str(fact["source_url"]) for fact in facts))
            if self._fits(
                insight_ids,
                query_sha256,
                as_of,
                stale_age_days,
                facts,
                [],
                [],
                source_urls,
            ):
                break
            facts.pop()
            if not facts:
                raise PanoramaContextError("panorama facts unavailable")
        fact_keys = {(item["insight_version_id"], item["fact_id"]) for item in facts}
        fact_sources = {
            (item["insight_version_id"], item["fact_id"]): {
                "source_url": item["source_url"],
                "observed_at": item["observed_at"],
            }
            for item in facts
        }
        inferences: list[dict[str, object]] = []
        for insight in insights:
            insight_id = str(insight.insight_version_id)
            for inference in insight.inferences:
                basis = tuple(
                    str(value) for value in inference.get("basis_fact_ids", ())
                )
                if not basis or any(
                    (insight_id, fact_id) not in fact_keys for fact_id in basis
                ):
                    continue
                text, truncated = _bounded_text(inference.get("text"), 1200)
                candidate = {
                    "insight_version_id": insight_id,
                    "text": text,
                    "basis_fact_ids": basis,
                    "basis_sources": tuple(
                        fact_sources[(insight_id, fact_id)] for fact_id in basis
                    ),
                    "truncated": truncated,
                }
                if self._fits(
                    insight_ids,
                    query_sha256,
                    as_of,
                    stale_age_days,
                    facts,
                    [*inferences, candidate],
                    [],
                    source_urls,
                ):
                    inferences.append(candidate)
        unknowns: list[dict[str, object]] = []
        for insight in insights:
            for unknown in insight.unknowns:
                text, truncated = _bounded_text(unknown.get("text"), 800)
                candidate = {
                    "insight_version_id": str(insight.insight_version_id),
                    "text": text,
                    "source_urls": (),
                    "evidence_status": "unverified",
                    "as_of": insight.created_at.isoformat(),
                    "truncated": truncated,
                }
                if self._fits(
                    insight_ids,
                    query_sha256,
                    as_of,
                    stale_age_days,
                    facts,
                    inferences,
                    [*unknowns, candidate],
                    source_urls,
                ):
                    unknowns.append(candidate)
        return PanoramaContextFragment(
            insight_ids,
            query_sha256,
            as_of,
            tuple(facts),
            tuple(inferences),
            tuple(unknowns),
            tuple(source_urls),
            stale_age_days,
        )

    @staticmethod
    def _fits(
        insight_ids,
        query_sha256,
        as_of,
        stale_age_days,
        facts,
        inferences,
        unknowns,
        source_urls,
    ) -> bool:
        try:
            fragment = PanoramaContextFragment(
                insight_ids,
                query_sha256,
                as_of,
                tuple(facts),
                tuple(inferences),
                tuple(unknowns),
                tuple(source_urls),
                stale_age_days,
            )
        except ValueError:
            return False
        document = fragment.as_prompt_document()
        return (
            _encoded_size(document) <= MAX_PANORAMA_CONTEXT_BYTES
            and _postgres_jsonb_text_size((document,)) <= MAX_PANORAMA_CONTEXT_BYTES
        )


__all__ = [
    "MAX_PANORAMA_CONTEXT_BYTES",
    "PanoramaContextError",
    "PanoramaContextFragment",
    "PanoramaContextProvider",
    "PanoramaContextSource",
]
