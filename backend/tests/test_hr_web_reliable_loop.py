# ruff: noqa: PLC0414
import os
from pathlib import Path

import psycopg
import pytest
from test_execution_worker_store import worker_database as worker_database
from test_hr_direct_worker import (
    attempt_repository as attempt_repository,
)
from test_hr_direct_worker import (
    control_database as control_database,
)
from test_hr_direct_worker import (
    conversation_database as conversation_database,
)
from test_hr_direct_worker import (
    direct_database as direct_database,
)
from test_hr_direct_worker import (
    repository as repository,
)
from test_hr_direct_worker import (
    worker_conversation as worker_conversation,
)

from tests.helpers.hr_web_loop import WebLoop, wait_until

pytestmark = pytest.mark.postgres


@pytest.fixture()
def web_loop(worker_conversation, direct_database):
    environment, owner, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set local role platform_control_owner")
        connection.execute(
            (
                Path(__file__).parents[1]
                / "control_migrations/pending/hr_web_result_recovery.sql"
            ).read_text()
        )
    loop = WebLoop(environment, owner, worker_conversation.conversation_id)
    try:
        yield loop
    finally:
        loop.close()


def test_public_snapshot_resolves_real_intake(web_loop):
    initial = web_loop.snapshot()
    turn = web_loop.submit("当前问题", "snapshot-intake")
    response = web_loop.client.get(
        f"/api/v1/conversations/{web_loop.conversation_id}/snapshot"
    )
    assert response.status_code == 200
    value = response.json()
    assert value["turn"]["turn_id"] == turn
    assert value["answer"] is None
    assert value["context_manifest_ref"] == f"context-manifest:intake:{turn}"
    assert value["read_version"] > initial["read_version"]
    assert web_loop.submit("当前问题", "snapshot-intake") == turn
    assert web_loop.snapshot()["read_version"] == value["read_version"]


def test_turn_message_read_is_not_limited_by_first_120_history_messages(
    web_loop, repository
):
    from uuid import uuid4

    from app.agent_brain.conversation_repository import message_subject

    # Owned history fixture only; no fabricated run, answer or terminal evidence.
    with psycopg.connect(web_loop.environment["admin"]) as connection:
        for seq in range(1, 121):
            message_id = uuid4()
            sealed = repository.content_codec.seal_json(
                message_subject(web_loop.conversation_id, message_id),
                {"text": "Owned historical input"},
            )
            connection.execute(
                "insert into platform_control.conversation_messages(message_id,conversation_id,seq,role,content_ciphertext,encryption_key_version,delivery_status,completed_at) values(%s,%s,%s,'user',%s,%s,'completed',now())",
                (
                    message_id,
                    web_loop.conversation_id,
                    seq,
                    sealed.ciphertext,
                    sealed.key_version,
                ),
            )
    turn = web_loop.submit("当前可见问题", "read-after-120")
    response = web_loop.client.get(
        f"/api/v1/conversations/{web_loop.conversation_id}/messages",
        params={"turn_id": turn},
    )
    assert response.status_code == 200
    assert [(value["seq"], value["content"]) for value in response.json()["items"]] == [
        (121, "当前可见问题")
    ]
    assert web_loop.snapshot(turn)["answer"] is None


def test_opted_in_public_new_hr_conversation_pins_worker_before_intake(web_loop):
    from uuid import uuid4

    response = web_loop.client.post(
        "/api/v1/agents/hr-bot/conversations",
        json={"text": "普通咨询"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201
    assert response.json()["conversation"]["execution_owner"] == "worker_direct"


def test_public_cancel_records_worker_intent_without_legacy_execution(web_loop):
    turn = web_loop.submit("稍后取消", "cancel-request")
    response = web_loop.client.post(
        f"/api/v1/conversations/{web_loop.conversation_id}/turns/current/cancel"
    )
    assert response.status_code == 200
    with psycopg.connect(web_loop.environment["admin"]) as connection:
        row = connection.execute(
            "select cancel_requested_at is not null,status from platform_control.turn_attempts where turn_id=%s",
            (turn,),
        ).fetchone()
    assert row == (True, "queued")


def test_real_worker_cancels_queued_turn_without_any_execution_capability(web_loop):
    with psycopg.connect(web_loop.environment["admin"]) as connection:
        connection.execute(
            "update platform_control.execution_workers set v5_observation=null"
        )
    turn = web_loop.submit("不用执行这项工作", "cancel-before-capability")
    web_loop.cancel()
    web_loop.start_direct()
    assert web_loop.wait_terminal(turn, "cancelled", timeout=10)["answer"] is None
    assert web_loop.binding(turn) is None
    assert web_loop.assistant_count(turn) == 0


def test_worker_http_snapshot_and_sse_never_advance_execution_state(web_loop):
    turn = web_loop.submit("读取不能执行这项工作", "pure-read")

    def execution_state():
        with psycopg.connect(web_loop.environment["admin"]) as connection:
            return tuple(
                connection.execute(
                    f"select coalesce(jsonb_agg(to_jsonb(row) order by to_jsonb(row)::text),'[]'::jsonb) from platform_control.{table} row"
                ).fetchone()[0]
                for table in (
                    "conversations",
                    "conversation_turns",
                    "turn_attempts",
                    "conversation_messages",
                    "conversation_events",
                    "missions",
                    "mission_tasks",
                    "mission_runs",
                    "execution_jobs",
                    "direct_command_bindings",
                )
            )

    before = execution_state()
    for _ in range(3):
        assert web_loop.snapshot(turn)["answer"] is None
        assert web_loop.messages()
        with web_loop.client.stream(
            "GET", f"/api/v1/conversations/{web_loop.conversation_id}/events"
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert next(response.iter_lines())
    assert execution_state() == before


@pytest.mark.skipif(
    not os.environ.get("HR_WEB_METABOT_ORIGIN"),
    reason="owned MetaBot process fixture required",
)
def test_actual_process_loop_same_request_and_api_restart(
    web_loop, worker_database, tmp_path
):
    # The original RED is now driven by actual workers, not a seeded answer.
    web_loop.start_workers(
        worker_database,
        os.environ["HR_WEB_METABOT_ORIGIN"],
        int(os.environ["HR_WEB_CALLBACK_PORT"]),
        tmp_path / "machine",
    )
    controls = Path(os.environ["HR_WEB_FIXTURE_ROOT"])
    web_loop.wait_ready()
    first = web_loop.submit("介绍一下你自己", "one-request")
    assert web_loop.submit("介绍一下你自己", "one-request") == first
    wait_until(
        lambda: (controls / "native-1-started").exists(),
        description="first native start",
    )
    (controls / "native-1-release").touch()
    before = web_loop.drive_until_available(first)
    assert before["answer"]["content"].strip()
    assert len(before["answer"]["content"].encode()) >= 32768
    answers = [
        message for message in web_loop.messages() if message["role"] == "assistant"
    ]
    assert len(answers) == 1
    web_loop.restart_api_process()
    assert web_loop.snapshot(first)["answer"] == before["answer"]
    assert [
        message for message in web_loop.messages() if message["role"] == "assistant"
    ] == answers
    assert web_loop.assistant_count(first) == 1
    web_loop.wait_terminal(first)

    # Kill the actual coordinator with a native invocation still alive. Its
    # successor must acquire a new lease but inspect the ORIGINAL run.
    second = web_loop.submit("继续介绍你的能力", "worker-killed")
    wait_until(
        lambda: (controls / "native-2-started").exists(),
        description="second native start",
    )
    original = wait_until(
        lambda: value if (value := web_loop.binding(second))["accepted_at"] else None
    )
    web_loop.restart_direct()
    recovered = wait_until(
        lambda: (
            value
            if (value := web_loop.binding(second))["lease_epoch"]
            > original["lease_epoch"]
            else None
        ),
        description="expired lease acquired by new worker",
    )
    assert recovered["run_id"] == original["run_id"]
    assert recovered["command_id"] == original["command_id"]
    assert recovered["executor_id"] != original["executor_id"]
    assert recovered["launch_lease_epoch"] == original["launch_lease_epoch"]
    (controls / "native-2-release").touch()
    assert (
        web_loop.drive_until_available(second)["answer"]["content"]
        == before["answer"]["content"]
    )
    web_loop.wait_terminal(second)
    assert web_loop.assistant_count(second) == 1

    # Actual API downtime DURING execution: the receiver must preserve the
    # provider's result before the cloud HTTP API is available again.
    third = web_loop.submit("说明你能协助哪些招聘工作", "api-killed-in-flight")
    wait_until(
        lambda: (controls / "native-3-started").exists(),
        description="third native start",
    )
    third_binding = wait_until(
        lambda: value if (value := web_loop.binding(third))["accepted_at"] else None
    )
    web_loop.stop_api_process()
    (controls / "native-3-release").touch()

    def receiver_has_result():
        with psycopg.connect(worker_database) as connection:
            return connection.execute(
                "select 1 from execution_worker.v5_callback_events where run_id=%s and event_json::jsonb->>'type'='result'",
                (third_binding["run_id"],),
            ).fetchone()

    wait_until(
        receiver_has_result,
        timeout=20,
        description="durable result with API process dead",
    )
    assert not web_loop.process.is_alive()
    web_loop.start_api()
    assert (
        web_loop.drive_until_available(third)["answer"]["content"]
        == before["answer"]["content"]
    )
    web_loop.wait_terminal(third)
    assert web_loop.assistant_count(third) == 1

    # The real MetaBot accepts, but its HTTP 202 is dropped before receiver
    # registration. Cancellation recovers the same acceptance and native stop.
    (controls / "drop-acceptance").touch()
    fourth = web_loop.submit("暂缓这次研究", "lost-acceptance-cancel")
    wait_until(
        lambda: (controls / "acceptance-dropped").exists(),
        description="lost real HTTP acceptance",
    )
    wait_until(
        lambda: (controls / "native-4-started").exists(),
        description="fourth native start",
    )
    assert web_loop.binding(fourth)["accepted_at"] is None
    web_loop.cancel()
    cancelled = web_loop.wait_terminal(fourth, "cancelled")
    assert cancelled["answer"] is None
    assert cancelled["outcome"]["kind"] == "cancelled"
    assert web_loop.binding(fourth)["executor_stop_proof_ref"]
    assert web_loop.assistant_count(fourth) == 0
    assert not (controls / "native-5-started").exists()
