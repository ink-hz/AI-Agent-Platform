"""Real uploaded material and DB; controlled authorization I/O, no model calls."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from uuid import uuid4

import pytest

from app.hr_agent.types import HrAgentProblem
from tests import test_hr_agent_materials as fixtures

uploaded = fixtures.uploaded
secured = fixtures.secured
database = fixtures.database


def history(uploaded, count=30):
    client, headers, repo, owner, materials, aid, _, _ = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    response = client.post(
        "/api/hr/agent/works",
        headers=headers,
        json={**fixtures.body(), "references": [ref]},
    )
    assert response.status_code == 201, response.text
    work = response.json()
    fence = repo.claim("history-performance", 60)
    # History load fixture: use the actual encrypted entry writer, no fake grants.
    with repo.transaction() as c:
        row = repo._fence(c, fence)
        for index in range(count):
            repo._entry(
                c, row, "note", {"body": f"public note {index}"}, refs=[ref], objects=[]
            )
    return repo, owner, work, fence, materials


@pytest.mark.parametrize("count", [1, 30, 300])
def test_repeated_material_history_has_bounded_authorization_reads(
    uploaded, monkeypatch, count
):
    repo, _, _, fence, materials = history(uploaded, count)
    original = materials.authorize_refs
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(materials, "authorize_refs", counted)

    def no_bytes(*args, **kwargs):
        pytest.fail("authorization reread original bytes")

    monkeypatch.setattr(materials, "_read_bytes", no_bytes)
    connects = []
    connect = repo.connection_factory

    def counted_connect():
        connects.append(1)
        return connect()

    monkeypatch.setattr(repo, "connection_factory", counted_connect)
    monkeypatch.setattr(materials, "connection_factory", counted_connect)
    entries = repo.read_selected_entries(fence)
    assert len(entries) == count + 1
    assert len(calls) <= 3, f"{len(calls)} checks for one unique reference"
    assert len(connects) <= 20, f"{len(connects)} connections for repeated reference"


@pytest.mark.parametrize("method", ["read_selected_entries", "context_input"])
def test_cancel_is_not_blocked_by_history_material_io(uploaded, monkeypatch, method):
    repo, owner, work, fence, materials = history(uploaded, 2)
    original = materials.authorize_refs
    entered, release = Event(), Event()

    def stalled(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test IO release timed out"
        return original(*args, **kwargs)

    monkeypatch.setattr(materials, "authorize_refs", stalled)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(getattr(repo, method), fence)
        assert entered.wait(3)
        cancellation = pool.submit(
            repo.cancel, owner, work["work_id"], "user cancelled", uuid4()
        )
        try:
            result = cancellation.result(timeout=1)
            assert result["state"] == "cancelled"
        finally:
            release.set()
        with pytest.raises(HrAgentProblem):
            reading.result(timeout=5)


def test_revocation_during_history_validation_prevents_return(
    uploaded, monkeypatch, database
):
    repo, _, _, fence, materials = history(uploaded, 2)
    original = materials.authorize_refs
    calls = 0

    def revoke(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            with database.admin_connection() as c:
                c.execute(
                    "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second'"
                )
        return result

    monkeypatch.setattr(materials, "authorize_refs", revoke)
    with pytest.raises(HrAgentProblem):
        repo.read_selected_entries(fence)


@pytest.mark.parametrize("method", ["read_selected_entries", "context_input"])
def test_new_input_during_unlocked_authorization_fences_old_context(
    uploaded, monkeypatch, method
):
    repo, owner, work, fence, materials = history(uploaded, 2)
    original = materials.authorize_refs
    entered, release, guard = Event(), Event(), Lock()
    first = True

    def stall_first(*args, **kwargs):
        nonlocal first
        with guard:
            pause = first
            first = False
        if pause:
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(materials, "authorize_refs", stall_first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(getattr(repo, method), fence)
        assert entered.wait(3)
        updating = pool.submit(
            repo.append_input,
            owner,
            work["work_id"],
            {
                "expected_input_revision": work["input_revision"],
                "text": "改为另一项工作，不再使用上一份材料",
                "objects": [],
                "references": [],
                "question_id": None,
            },
            uuid4(),
        )
        try:
            assert (
                updating.result(timeout=1)["input_revision"]
                == work["input_revision"] + 1
            )
        finally:
            release.set()
        with pytest.raises(HrAgentProblem):
            reading.result(timeout=5)


def test_history_decisions_do_not_outlive_one_read(uploaded, database):
    repo, _, _, fence, _ = history(uploaded, 2)
    assert repo.read_selected_entries(fence)
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second'"
        )
    with pytest.raises(HrAgentProblem):
        repo.read_selected_entries(fence)


def test_cancel_receipt_does_not_erase_persisted_reading_checkpoint(uploaded):
    from app.hr_agent.types import validate_contract

    repo, owner, work, _, _ = history(uploaded, 2)
    result = repo.cancel(owner, work["work_id"], "stop", uuid4())
    validate_contract("WorkView", result)
    assert result["state"] == "cancelled" and result["checkpoint"]["readings"] == []
    with repo.transaction() as c:
        row = repo._work(c, owner, work["work_id"])
        checkpoint = repo._unseal("works", row["work_id"], "sealed_checkpoint", row)
    assert checkpoint["readings"], "control receipt must not overwrite stored coverage"


def test_original_deleted_during_final_scope_rebuilds_nested_summary(
    uploaded, database, monkeypatch
):
    from app.hr_agent.types import ContextRebuildRequired

    repo, _, _, fence, _ = history(uploaded, 0)
    # Synthetic history uses the real encrypted writer. No model reply or grant
    # is fabricated: the uploaded material remains currently authorized.
    with repo.transaction() as c:
        work = repo._fence(c, fence)
        c.execute(
            "SELECT * FROM platform_hr_agent.entries WHERE work_id=%s ORDER BY seq",
            (work["work_id"],),
        )
        original = c.fetchone()
        previous = original
        for text in ("第一层合成摘要", "第二层合成摘要"):
            summary_id = repo._entry(
                c,
                work,
                "summary",
                {"body": text},
                refs=original["source_refs"],
                objects=original["objects"],
                provenance={
                    "derived_from": [
                        {
                            "entry_id": str(previous["entry_id"]),
                            "seq": previous["seq"],
                            "input_revision": previous["input_revision"],
                        }
                    ],
                    "policy_revision": "synthetic-history-test",
                },
            )
            c.execute(
                "SELECT * FROM platform_hr_agent.entries WHERE entry_id=%s",
                (summary_id,),
            )
            previous = c.fetchone()

    unseal = repo._unseal
    scope = repo._scope
    summary_decrypted = deleted = False

    def observe_unseal(table, identity, field, row):
        nonlocal summary_decrypted
        result = unseal(table, identity, field, row)
        if table == "entries" and identity == summary_id and field == "sealed_body":
            summary_decrypted = True
        return result

    def delete_after_scope(*args, **kwargs):
        nonlocal deleted
        result = scope(*args, **kwargs)
        # Only the final scope validation occurs after the selected summary body
        # was decrypted. Delete its transitive origin in a separate committed TX.
        if summary_decrypted and not deleted:
            with database.admin_connection() as c:
                cursor = c.execute(
                    "DELETE FROM platform_hr_agent.entries WHERE entry_id=%s",
                    (original["entry_id"],),
                )
                assert cursor.rowcount == 1
            deleted = True
        return result

    monkeypatch.setattr(repo, "_unseal", observe_unseal)
    monkeypatch.setattr(repo, "_scope", delete_after_scope)
    with pytest.raises(ContextRebuildRequired):
        repo.read_selected_entries(fence)
    assert summary_decrypted and deleted
    # A rebuild omits both summaries now that the original is absent.
    assert repo.read_selected_entries(fence) == ()
