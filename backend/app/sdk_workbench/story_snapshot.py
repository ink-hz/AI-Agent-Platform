"""Read-only evidence collection over an injected native-shaped reader."""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

from fastapi import HTTPException

from .zentao import (ZenTaoAuthenticationError, ZenTaoProtocolError, ZenTaoRejected,
                     ZenTaoRequestRejected, ZenTaoUnavailable)


_DIGITS = re.compile(r"[0-9]+\Z")
_HTTP_CODES = {
    (403, "sdk_sso_access_or_binding_required"): "access_or_binding_required",
    (503, "sdk_sso_identity_stale"): "identity_stale",
    (503, "sdk_sso_clock_unavailable"): "identity_unavailable",
    (503, "sdk_sso_zentao_unavailable"): "source_unavailable",
    (502, "sdk_sso_zentao_rejected"): "source_rejected",
    (502, "sdk_sso_zentao_response_invalid"): "source_protocol_error",
}


@dataclass(frozen=True)
class SnapshotScope:
    instance_id: str
    declared_zentao_version: str
    product_id: int
    actor_ref: str
    visibility_ref: str

    def __post_init__(self) -> None:
        refs = (self.instance_id, self.declared_zentao_version, self.actor_ref, self.visibility_ref)
        if (any(not isinstance(value, str) or not value.strip() for value in refs)
                or type(self.product_id) is not int or self.product_id < 1):
            raise ValueError("snapshot_scope_invalid")


class StoryReader(Protocol):
    async def request(self, actor: object, method: str, path: str,
                      payload: dict | None = None, params: dict | None = None,
                      *, api_version: int = 2) -> dict:
        """Return the native response; authentication remains caller-owned."""
        ...


class _PageInvalid(ValueError):
    def __init__(self, code: str, path: str) -> None:
        super().__init__(code)
        self.code = code
        self.path = path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _integer(value: object, code: str, path: str, *, positive: bool = False) -> int:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and _DIGITS.fullmatch(value):
        try:
            number = int(value)
        except ValueError:
            raise _PageInvalid(code, path) from None
    else:
        raise _PageInvalid(code, path)
    if number < int(positive):
        raise _PageInvalid(code, path)
    return number


def _parse_reported(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("pager"), dict):
        raise _PageInvalid("pager_invalid", "pager")
    return {
        "page_total": _integer(raw["pager"].get("pageTotal"), "pager_invalid", "pager.pageTotal"),
        "rec_total": _integer(raw["pager"].get("recTotal"), "pager_invalid", "pager.recTotal"),
    }


def _validate_page(body, totals, pager_error, page_id, product_id, baseline, prior, accepted_count):
    if (not isinstance(body, dict) or body.get("status") != "success"
            or not isinstance(body.get("stories"), list) or not isinstance(body.get("pager"), dict)):
        raise _PageInvalid("response_invalid", "response")
    stories, pager = body["stories"], body["pager"]
    if pager_error is not None:
        raise pager_error
    if baseline is not None and totals != baseline:
        raise _PageInvalid("pager_drift", "pager")
    pages, records = totals["page_total"], totals["rec_total"]
    if pages > 20 or records > 1000:
        raise _PageInvalid("catalog_limit_exceeded", "pager")
    if len(stories) > 50:
        raise _PageInvalid("page_size_exceeded", "stories")
    if (records == 0 and (pages not in (0, 1) or stories or page_id != 1)
            or records > 0 and not (1 <= pages <= records <= pages * 50)):
        raise _PageInvalid("pager_inconsistent", "pager")
    if "pageID" in pager and _integer(pager["pageID"], "pager_invalid", "pager.pageID", positive=True) != page_id:
        raise _PageInvalid("pager_page_mismatch", "pager.pageID")
    if "recPerPage" in pager and _integer(pager["recPerPage"], "pager_invalid", "pager.recPerPage", positive=True) != 50:
        raise _PageInvalid("pager_size_mismatch", "pager.recPerPage")
    staged = {}
    for index, item in enumerate(stories):
        path = f"stories[{index}]"
        if not isinstance(item, dict):
            raise _PageInvalid("story_invalid", path)
        _integer(item.get("id"), "story_id_invalid", f"{path}.id", positive=True)
        key = str(item["id"])
        owner = _integer(item.get("product"), "story_product_invalid", f"{path}.product", positive=True)
        if owner != product_id:
            raise _PageInvalid("story_product_mismatch", f"{path}.product")
        previous = staged.get(key, prior.get(key))
        if previous is not None:
            code = "duplicate_id" if previous == item else "duplicate_id_conflict"
            raise _PageInvalid(code, f"{path}.id")
        staged[key] = item
    count = accepted_count + len(stories)
    if count > records:
        raise _PageInvalid("count_exceeded", "stories")
    final = page_id >= max(1, pages)
    if final and count != records:
        raise _PageInvalid("count_mismatch", "stories")
    if not final and not stories:
        raise _PageInvalid("empty_intermediate_page", "stories")
    return totals, staged, final


async def collect_story_snapshot(reader: StoryReader, actor: object, *, scope: SnapshotScope) -> dict:
    if not isinstance(scope, SnapshotScope):
        raise ValueError("snapshot_scope_invalid")
    if actor is None:
        raise ValueError("snapshot_actor_required")
    report = {
        "schema_version": "r0b-story-snapshot-1", "scope": asdict(scope),
        "scope_verification": "caller_declared_not_verified",
        "started_at": _now(), "finished_at": None, "outcome": "failed",
        "reported": None, "last_valid_page": 0, "stopped_page": None,
        "pages": [], "stories": [], "issues": [],
        "counts": {"accepted_pages": 0, "received_items": 0, "accepted_stories": 0},
        "boundaries": {
            "atomic": False,
            "completeness": "reported_pages_and_count_in_declared_authorized_view",
            "permission_filtered_records": "unknown", "unseen_deleted_records": "unknown",
            "deletion_inference": False, "business_state_mapping": "not_performed",
            "production_import_eligible": False,
        },
    }
    seen = {}
    page_id = 1
    baseline = None
    while True:
        evidence = {"page_id": page_id, "requested_at": _now(), "received_at": None,
                    "raw_response": None, "reported": None, "outcome": "source_error"}
        report["pages"].append(evidence)
        source_error = None
        try:
            body = await reader.request(actor, "GET", f"products/{scope.product_id}/stories",
                params={"browseType": "allstory", "recPerPage": 50, "pageID": page_id}, api_version=2)
        except ZenTaoAuthenticationError:
            source_error = "authentication_failed"
        except ZenTaoRequestRejected:
            source_error = "request_rejected"
        except ZenTaoRejected:
            source_error = "source_rejected"
        except ZenTaoUnavailable:
            source_error = "source_unavailable"
        except ZenTaoProtocolError:
            source_error = "source_protocol_error"
        except HTTPException as error:
            source_error = (_HTTP_CODES.get((error.status_code, error.detail))
                            if isinstance(error.detail, str) else None)
            if source_error is None:
                raise
        if source_error is not None:
            report["issues"].append({"code": source_error, "path": "reader.request", "page_id": page_id})
            break
        evidence["received_at"] = _now()
        evidence["raw_response"] = copy.deepcopy(body)
        evidence["outcome"] = "invalid"
        raw = evidence["raw_response"]
        if isinstance(raw, dict) and isinstance(raw.get("stories"), list):
            report["counts"]["received_items"] += len(raw["stories"])
        # Parse controls once, preserving them even if stories are invalid.
        totals = None
        pager_error = None
        try:
            totals = _parse_reported(raw)
        except _PageInvalid as error:
            pager_error = error
        evidence["reported"] = copy.deepcopy(totals)
        if report["reported"] is None and totals is not None:
            report["reported"] = copy.deepcopy(totals)
        try:
            totals, staged, final = _validate_page(raw, totals, pager_error, page_id,
                                                  scope.product_id, baseline, seen,
                                                  len(report["stories"]))
        except _PageInvalid as error:
            report["issues"].append({"code": error.code, "path": error.path, "page_id": page_id})
            break
        baseline = totals
        seen.update(staged)
        report["stories"].extend(copy.deepcopy(raw["stories"]))
        report["last_valid_page"] = page_id
        report["counts"]["accepted_pages"] += 1
        report["counts"]["accepted_stories"] = len(report["stories"])
        evidence["outcome"] = "accepted"
        if final:
            report["outcome"] = "complete"
            break
        page_id += 1
    if report["outcome"] != "complete":
        report["outcome"] = "partial" if report["last_valid_page"] else "failed"
        report["stopped_page"] = page_id
    report["finished_at"] = _now()
    return report
