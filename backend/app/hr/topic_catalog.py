"""Validate producer-authored topic semantics without inferring business relations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from uuid import UUID

_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")
_CLAIM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TOPIC_KEYS = {
    "topic_id",
    "title",
    "question",
    "scope",
    "analysis_state",
    "unit_ids",
    "discussed_companies",
    "limitations",
}
_CLAIM_FIELDS = {
    "facts": "fact_id",
    "inferences": "inference_id",
    "recommendations": "recommendation_id",
    "alternatives": "alternative_id",
}


class TopicCatalogError(ValueError):
    pass


def _text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > 4096
        or _CONTROL.search(value)
    ):
        raise TopicCatalogError("topic text invalid")
    return value


def _key(value: object) -> str:
    value = _text(value)
    if not _KEY.fullmatch(value):
        raise TopicCatalogError("topic identifier invalid")
    return value


def _uuid(value: object) -> str:
    value = _text(value)
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise TopicCatalogError("topic unit identity invalid") from None
    return value


def _list(value: object) -> list:
    if not isinstance(value, list):
        raise TopicCatalogError("topic list invalid")
    return value


def _unique(value: object, validator=_text) -> list[str]:
    items = [validator(item) for item in _list(value)]
    if len(set(items)) != len(items):
        raise TopicCatalogError("topic duplicate identifier")
    return items


def validate_topic_catalog(catalog: Mapping, analyses: Sequence) -> list[dict]:
    """Return a detached catalog; absent metadata is explicitly an empty catalog."""
    if "topics" not in catalog:
        return []
    topics = _list(catalog["topics"])
    companies = [
        _key(item.get("company_key"))
        for item in _list(catalog.get("companies"))
        if isinstance(item, Mapping)
    ]
    if len(companies) != len(catalog["companies"]) or len(set(companies)) != len(
        companies
    ):
        raise TopicCatalogError("topic company catalog invalid")
    units = {}
    for unit in analyses:
        if not isinstance(unit, Mapping):
            raise TopicCatalogError("topic analysis invalid")
        identity = _uuid(unit.get("unit_id"))
        if identity in units:
            raise TopicCatalogError("topic duplicate unit")
        units[identity] = unit
    identities = set()
    for topic in topics:
        if not isinstance(topic, Mapping) or set(topic) != _TOPIC_KEYS:
            raise TopicCatalogError("topic fields invalid")
        identity = _key(topic["topic_id"])
        if identity in identities:
            raise TopicCatalogError("topic duplicate identity")
        identities.add(identity)
        _text(topic["title"])
        _text(topic["question"])
        scope = topic["scope"]
        if not isinstance(scope, Mapping) or set(scope) != {
            "description",
            "company_keys",
            "tracks",
        }:
            raise TopicCatalogError("topic scope invalid")
        _text(scope["description"])
        scoped_companies = _unique(scope["company_keys"], _key)
        if not set(scoped_companies).issubset(companies):
            raise TopicCatalogError("topic company unknown")
        _unique(scope["tracks"], _key)
        _unique(topic["limitations"])
        state = _text(topic["analysis_state"])
        if state not in {"available", "limited", "insufficient_evidence", "missing"}:
            raise TopicCatalogError("topic analysis state invalid")
        unit_ids = _unique(topic["unit_ids"], _uuid)
        if bool(unit_ids) != (state != "missing"):
            raise TopicCatalogError("topic analysis state and units mismatch")
        for unit_id in unit_ids:
            if unit_id not in units or units[unit_id].get("kind") not in {
                "topic",
                "track",
            }:
                raise TopicCatalogError("topic analysis unit invalid")
        relationships = set()
        for relation in _list(topic["discussed_companies"]):
            if not isinstance(relation, Mapping) or set(relation) != {
                "company_key",
                "unit_id",
                "claim_ids",
                "explanation",
            }:
                raise TopicCatalogError("topic relationship invalid")
            company = _key(relation["company_key"])
            unit_id = _uuid(relation["unit_id"])
            if company not in scoped_companies or unit_id not in unit_ids:
                raise TopicCatalogError("topic relationship outside declared scope")
            if (company, unit_id) in relationships:
                raise TopicCatalogError("topic duplicate relationship")
            relationships.add((company, unit_id))
            _text(relation["explanation"])
            claim_ids = _unique(relation["claim_ids"])
            if not claim_ids or any(not _CLAIM.fullmatch(item) for item in claim_ids):
                raise TopicCatalogError("topic claim identity invalid")
            response = units[unit_id].get("response")
            if not isinstance(response, Mapping):
                raise TopicCatalogError("topic response invalid")
            known = []
            for section, id_field in _CLAIM_FIELDS.items():
                for claim in _list(response.get(section, [])):
                    if not isinstance(claim, Mapping):
                        raise TopicCatalogError("topic claim invalid")
                    known.append(claim.get(id_field))
            if any(known.count(claim_id) != 1 for claim_id in claim_ids):
                raise TopicCatalogError("topic dangling or duplicate claim")
    return deepcopy([dict(topic) for topic in topics])


def validate_topic_provenance(
    manifest: Mapping, analyses: Sequence, *, analysis_sha256: str, topics: list[dict]
) -> None:
    """Check internal consistency of explicit analysis origins in derived publications.

    The source manifest digest is a traceable identity, not an assertion that an
    arbitrary source package has been obtained or independently trusted.
    """
    provenance = manifest.get("provenance")
    if provenance is None:
        return
    if (
        not isinstance(provenance, Mapping)
        or provenance.get("kind") != "reviewed_topic_catalog"
    ):
        raise TopicCatalogError("topic provenance invalid")
    try:
        source_id = _uuid(provenance.get("source_bundle_id"))
        analysis_id = _uuid(provenance.get("analysis_bundle_id"))
        _uuid(provenance.get("origin_documents_bundle_id"))
    except ValueError:
        raise TopicCatalogError("topic provenance identity invalid") from None
    if source_id == manifest.get("bundle_id"):
        raise TopicCatalogError("topic provenance source cannot be self")
    for field in (
        "source_manifest_sha256",
        "analysis_sha256",
        "reviewed_catalog_sha256",
    ):
        value = provenance.get(field)
        if not isinstance(value, str) or re.fullmatch("[a-f0-9]{64}", value) is None:
            raise TopicCatalogError("topic provenance digest invalid")
    reviewed_body = (
        json.dumps(
            topics,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if (
        provenance["reviewed_catalog_sha256"]
        != hashlib.sha256(reviewed_body).hexdigest()
        or provenance["analysis_sha256"] != analysis_sha256
        or any(unit.get("bundle_id") != analysis_id for unit in analyses)
        or provenance.get("analysis_generated_at") != manifest.get("generated_at")
        or provenance.get("origin_documents") != manifest.get("document_index")
    ):
        raise TopicCatalogError("topic provenance does not match preserved analysis")
