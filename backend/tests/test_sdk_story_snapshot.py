from __future__ import annotations

import asyncio
import copy
import socket
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.sdk_workbench.story_snapshot import SnapshotScope, collect_story_snapshot
from app.sdk_workbench.zentao import (
    ZenTaoAuthenticationError, ZenTaoProtocolError, ZenTaoRejected,
    ZenTaoRequestRejected, ZenTaoUnavailable,
)


SCOPE = SnapshotScope("test-instance-ref", "declared-test-version", 1,
                      "test-actor-ref", "test-visible-product-ref")
ACTOR = object()


def story(native_id, **extra):
    return {"id": native_id, "product": 1, "status": "", "version": "2",
            "lastEditedDate": "2026-10-22 10:00:00", "deleted": "0", **extra}


def page(items, total=None, pages=1, **pager_extra):
    return {"status": "success", "stories": items,
            "pager": {"pageTotal": pages,
                      "recTotal": len(items) if total is None else total, **pager_extra}}


class Reader:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def request(self, actor, method, path, payload=None, params=None, *, api_version=2):
        self.calls.append((actor, method, path, payload, copy.deepcopy(params), api_version))
        result = self.responses[len(self.calls) - 1]
        if isinstance(result, BaseException):
            raise result
        return result


def run(responses):
    reader = Reader(responses)
    return asyncio.run(collect_story_snapshot(reader, ACTOR, scope=SCOPE)), reader


def test_multpage_preserves_native_values_scope_and_get_identity_contract():
    responses = [page([story("03", deleted="1"), story(4)], total="3", pages="2"),
                 page([story(5, status="closed", sourceNote="任意原值")], total=3, pages=2)]
    before = copy.deepcopy(responses)
    report, reader = run(responses)
    assert responses == before
    assert report["outcome"] == "complete"
    assert report["last_valid_page"] == 2 and report["stopped_page"] is None
    assert report["reported"] == {"page_total": 2, "rec_total": 3}
    assert report["stories"] == before[0]["stories"] + before[1]["stories"]
    assert report["counts"] == {"accepted_pages": 2, "received_items": 3, "accepted_stories": 3}
    assert report["scope"]["actor_ref"] == SCOPE.actor_ref
    assert report["scope_verification"] == "caller_declared_not_verified"
    for n, call in enumerate(reader.calls, 1):
        assert call[0] is ACTOR
        assert call[1:] == ("GET", "products/1/stories", None,
                           {"browseType": "allstory", "recPerPage": 50, "pageID": n}, 2)
    assert report["pages"][0]["raw_response"] == before[0]
    responses[0]["stories"][0]["version"] = "later-mutation"
    assert report["stories"][0]["version"] == "2"
    assert report["pages"][0]["raw_response"]["stories"][0]["version"] == "2"
    assert report["boundaries"] == {
        "atomic": False,
        "completeness": "reported_pages_and_count_in_declared_authorized_view",
        "permission_filtered_records": "unknown", "unseen_deleted_records": "unknown",
        "deletion_inference": False, "business_state_mapping": "not_performed",
        "production_import_eligible": False,
    }
    for key in ("started_at", "finished_at"):
        assert datetime.fromisoformat(report[key]).utcoffset().total_seconds() == 0


@pytest.mark.parametrize("pages", [0, 1, "0", "1"])
def test_empty_catalog_is_complete_in_declared_view(pages):
    report, reader = run([page([], total=0, pages=pages)])
    assert report["outcome"] == "complete" and report["stories"] == []
    assert report["last_valid_page"] == 1 and len(reader.calls) == 1


@pytest.mark.parametrize("changed,code", [
    (lambda: page([story(2)], total=4, pages=2), "pager_drift"),
    (lambda: page([story(2)], total=3, pages=3), "pager_drift"),
    (lambda: page([story(1)], total=3, pages=2), "duplicate_id"),
    (lambda: page([story(1, status="active")], total=3, pages=2), "duplicate_id_conflict"),
    (lambda: page([story(2), None], total=3, pages=2), "story_invalid"),
    (lambda: page([story(2, product=9), story(3)], total=3, pages=2), "story_product_mismatch"),
    (lambda: page([story(2)], total=3, pages=2), "count_mismatch"),
])
def test_bad_second_page_retains_raw_and_last_valid_progress(changed, code):
    bad = changed()
    report, reader = run([page([story(1)], total=3, pages=2), bad])
    assert report["outcome"] == "partial"
    assert report["last_valid_page"] == 1 and report["stopped_page"] == 2
    assert report["stories"] == [story(1)]
    assert report["pages"][1]["raw_response"] == bad
    assert report["pages"][1]["outcome"] == "invalid"
    assert report["issues"][0]["code"] == code and len(reader.calls) == 2


@pytest.mark.parametrize("error,code", [
    (ZenTaoAuthenticationError("private-token"), "authentication_failed"),
    (ZenTaoRequestRejected("private-token"), "request_rejected"),
    (ZenTaoRejected("private-token"), "source_rejected"),
    (ZenTaoUnavailable("private-token"), "source_unavailable"),
    (ZenTaoProtocolError("private-token"), "source_protocol_error"),
    (HTTPException(403, "sdk_sso_access_or_binding_required"), "access_or_binding_required"),
    (HTTPException(503, "sdk_sso_identity_stale"), "identity_stale"),
    (HTTPException(503, "sdk_sso_clock_unavailable"), "identity_unavailable"),
    (HTTPException(503, "sdk_sso_zentao_unavailable"), "source_unavailable"),
    (HTTPException(502, "sdk_sso_zentao_rejected"), "source_rejected"),
    (HTTPException(502, "sdk_sso_zentao_response_invalid"), "source_protocol_error"),
])
@pytest.mark.parametrize("after_page", [False, True])
def test_explicit_source_failure_has_no_retry_and_retains_prior_page(error, code, after_page):
    responses = [page([story(1)], total=2, pages=2)] if after_page else []
    report, reader = run(responses + [error])
    assert report["outcome"] == ("partial" if after_page else "failed")
    assert report["last_valid_page"] == int(after_page)
    assert report["stopped_page"] == 1 + int(after_page)
    assert report["issues"] == [{"code": code, "path": "reader.request",
                                  "page_id": 1 + int(after_page)}]
    assert report["pages"][-1]["raw_response"] is None
    assert report["pages"][-1]["outcome"] == "source_error"
    assert "private-token" not in repr(report)
    assert len(reader.calls) == len(responses) + 1


@pytest.mark.parametrize("error", [RuntimeError("unexpected"), ValueError("bug"),
                                  HTTPException(500, "bug"), HTTPException(403, "unknown-detail"),
                                  HTTPException(503, {"unexpected": True}), asyncio.CancelledError()])
def test_unexpected_errors_and_cancellation_escape(error):
    with pytest.raises(type(error)) as caught:
        run([error])
    assert caught.value is error


@pytest.mark.parametrize("body,code", [
    (None, "response_invalid"),
    ({"status": "fail", "stories": [], "pager": {}}, "response_invalid"),
    ({"status": "success", "stories": {}, "pager": {}}, "response_invalid"),
    ({"status": "success", "stories": [], "pager": []}, "response_invalid"),
    (page([], total=True), "pager_invalid"),
    (page([], pages=-1), "pager_invalid"),
    (page([], total=0.0), "pager_invalid"),
    (page([], pages="x"), "pager_invalid"),
    (page([], pages="9" * 5000), "pager_invalid"),
    ({"status": "success", "stories": [], "pager": {"pageTotal": 1}}, "pager_invalid"),
    (page([story(1)], pageID=2), "pager_page_mismatch"),
    (page([story(1)], recPerPage=25), "pager_size_mismatch"),
    (page([], total=0, pages=2), "pager_inconsistent"),
    (page([story(1)], total=1, pages=0), "pager_inconsistent"),
    (page([None]), "story_invalid"),
    (page([{}]), "story_id_invalid"),
    (page([story(True)]), "story_id_invalid"),
    (page([story(0)]), "story_id_invalid"),
    (page([story(" 1")]), "story_id_invalid"),
    (page([story(1, product=None)]), "story_product_invalid"),
    (page([story(1, product=True)]), "story_product_invalid"),
    (page([story(1, product=2)]), "story_product_mismatch"),
    (page([story(1), story(1)]), "duplicate_id"),
    (page([story(1), story(1, version=3)]), "duplicate_id_conflict"),
    (page([story(1), story(2)], total=1), "count_exceeded"),
    (page([story(1)], total=2), "count_mismatch"),
    (page([], total=2, pages=2), "empty_intermediate_page"),
])
def test_bad_first_page_fails_without_silently_accepting_items(body, code):
    report, reader = run([body])
    assert report["outcome"] == "failed" and report["last_valid_page"] == 0
    assert report["stories"] == [] and report["counts"]["accepted_pages"] == 0
    assert report["pages"][0]["raw_response"] == body
    assert report["issues"][0]["code"] == code and len(reader.calls) == 1


@pytest.mark.parametrize("body,code", [
    (page([story(1)], total=1001, pages=20), "catalog_limit_exceeded"),
    (page([story(1)], total=1000, pages=21), "catalog_limit_exceeded"),
    (page([story(n) for n in range(1, 52)], total=51, pages=2), "page_size_exceeded"),
])
def test_limits_stop_after_first_response(body, code):
    report, reader = run([body])
    assert report["outcome"] == "failed" and report["issues"][0]["code"] == code
    assert len(reader.calls) == 1 and report["pages"][0]["raw_response"] == body


def test_exact_1000_story_boundary_completes_without_page_21():
    report, reader = run([page([story(n) for n in range(start, start + 50)], total=1000, pages=20)
                          for start in range(1, 1001, 50)])
    assert report["outcome"] == "complete" and len(report["stories"]) == 1000
    assert report["last_valid_page"] == 20 and len(reader.calls) == 20


@pytest.mark.parametrize("field,value", [("instance_id", ""), ("declared_zentao_version", None),
    ("actor_ref", " "), ("visibility_ref", 4), ("product_id", True), ("product_id", 0)])
def test_scope_is_explicit_and_rejects_invalid_input(field, value):
    values = dict(instance_id="i", declared_zentao_version="v", product_id=1,
                  actor_ref="a", visibility_ref="s")
    values[field] = value
    with pytest.raises(ValueError, match="snapshot_scope_invalid"):
        SnapshotScope(**values)


def test_missing_actor_fails_before_reader_call():
    reader = Reader([])
    with pytest.raises(ValueError, match="snapshot_actor_required"):
        asyncio.run(collect_story_snapshot(reader, None, scope=SCOPE))
    assert reader.calls == []


def test_scope_and_collection_do_not_open_files_network_or_client(monkeypatch):
    import builtins
    import httpx
    import app.sdk_workbench.story_snapshot as module

    def forbidden(*args, **kwargs):
        pytest.fail("external I/O/client construction forbidden")

    reader = Reader([page([story(1)])])
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(socket.socket, "connect", forbidden)
        patch.setattr(socket.socket, "connect_ex", forbidden)
        patch.setattr(httpx, "Client", forbidden)
        patch.setattr(httpx, "AsyncClient", forbidden)
        scope = module.SnapshotScope("i", "v", 1, "a", "s")
        assert reader.calls == []
        report = asyncio.run(module.collect_story_snapshot(reader, ACTOR, scope=scope))
    assert report["outcome"] == "complete"


def test_numeric_id_representation_conflict_preserves_both_raw_values():
    body = page([story(1), story("1")])
    report, _ = run([body])
    assert report["issues"][0]["code"] == "duplicate_id_conflict"
    assert report["pages"][0]["raw_response"] == body


@pytest.mark.parametrize("pager,reported", [
    ({"pageTotal": "1", "recTotal": "0"}, {"page_total": 1, "rec_total": 0}),
    ({"pageTotal": "bad", "recTotal": 0}, None),
])
def test_invalid_stories_retains_parsed_controls_and_response_error_priority(pager, reported):
    body = {"status": "success", "stories": {}, "pager": pager}
    report, _ = run([body])
    assert report["outcome"] == "failed"
    assert report["issues"][0]["code"] == "response_invalid"
    assert report["reported"] == reported
    assert report["pages"][0]["reported"] == reported
    assert report["pages"][0]["raw_response"] == body
