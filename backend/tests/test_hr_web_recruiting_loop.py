"""Synthetic model execution; real authenticated two-turn Position business path."""

# ruff: noqa: PLC0414
import hashlib
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from test_hr_p0_recruiting_loop import POSITION_PACKAGE
from test_hr_web_reliable_loop import (
    attempt_repository as attempt_repository,
)
from test_hr_web_reliable_loop import (
    control_database as control_database,
)
from test_hr_web_reliable_loop import (
    conversation_database as conversation_database,
)
from test_hr_web_reliable_loop import (
    direct_database as direct_database,
)
from test_hr_web_reliable_loop import (
    repository as repository,
)
from test_hr_web_reliable_loop import (
    worker_conversation as worker_conversation,
)
from test_hr_web_reliable_loop import (
    worker_database as worker_database,
)

from app.hr.structured_output import encode_hr_envelope
from tests.helpers.hr_recruiting_loop import RecruitingWebLoop
from tests.helpers.hr_web_loop import wait_until

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("HR_WEB_METABOT_ORIGIN"),
        reason="owned real MetaBot recruiting fixture required",
    ),
]


@pytest.fixture()
def recruiting_loop(worker_conversation, direct_database):
    environment, owner, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set local role platform_control_owner")
        connection.execute(
            (
                Path(__file__).parents[1]
                / "control_migrations/pending/hr_web_result_recovery.sql"
            ).read_text()
        )
    loop = RecruitingWebLoop(environment, owner, worker_conversation.conversation_id)
    try:
        yield loop
    finally:
        loop.close()


def interview_pdf():
    document = io.BytesIO()
    pdf = canvas.Canvas(document, invariant=True)
    pdf.drawString(40, 780, "Synthetic interview questions")
    pdf.drawString(40, 750, "Explain your extrusion-system mass-production experience.")
    pdf.drawString(40, 725, "How did you improve manufacturing yield?")
    pdf.save()
    return document.getvalue()


def test_real_web_position_draft_confirmation_and_next_turn_context(
    recruiting_loop, worker_database, tmp_path
):
    loop = recruiting_loop
    controls = Path(os.environ["HR_WEB_FIXTURE_ROOT"])
    (controls / "platform-origin").write_text(loop.origin)
    package_answer = "岗位方案已生成。\n\n" + encode_hr_envelope(
        "position_package", POSITION_PACKAGE
    )
    (controls / "answer-1.txt").write_text(package_answer)
    (controls / "answer-2.txt").write_text("已依据已确认的 JD/JR 整理下一步招聘建议。")
    loop.start_workers(
        worker_database,
        os.environ["HR_WEB_METABOT_ORIGIN"],
        int(os.environ["HR_WEB_CALLBACK_PORT"]),
        tmp_path / "machine",
    )
    loop.wait_ready()
    first = loop.submit(
        "请形成高级结构工程师的完整岗位方案、JD 和 JR。", "position-draft"
    )
    answer = loop.drive_until_available(first)
    assert answer["answer"]["content"] == package_answer
    assert answer["deliveries"] == []
    loop.wait_terminal(first)

    def projected_package():
        response = loop.client.get(
            f"/api/hr/conversations/{loop.conversation_id}/position-package"
        )
        assert response.status_code in (200, 404), response.text
        return response.json() if response.status_code == 200 else None

    package = wait_until(
        projected_package,
        timeout=15,
        description="real leased position package projection",
    )
    assert package["modules"] == POSITION_PACKAGE["modules"]
    assert loop.position_ids() == []
    first_capture = json.loads((controls / "prompt-1.json").read_text())
    assert first_capture["model"] == "claude-opus-4-8"
    first_context = json.loads(first_capture["prompt"]["message"]["content"])
    assert first_context["hr_position_context"] is None
    assert "position_package" in first_context["hr_workflow_contract"]
    with psycopg.connect(loop.environment["admin"]) as connection:
        assert connection.execute(
            "select model_version,source_turn_id::text,source_assistant_message_id::text "
            "from platform_hr.position_draft_versions where draft_version_id=%s",
            (package["draft_version_id"],),
        ).fetchone() == ("claude-opus-4-8", first, answer["answer"]["message_id"])
    confirmation_path = (
        f"/api/hr/position-drafts/{package['draft_id']}/versions/"
        f"{package['draft_version_id']}/confirm"
    )
    body = {"expected_row_version": package["row_version"]}
    request_key = str(uuid4())
    denied = loop.client.post(
        confirmation_path,
        json=body,
        headers={"Idempotency-Key": request_key, "X-CSRF-Token": "wrong"},
    )
    assert denied.status_code == 403
    assert loop.position_ids() == []
    confirmed = loop.client.post(
        confirmation_path, json=body, headers={"Idempotency-Key": request_key}
    )
    assert confirmed.status_code == 200, confirmed.text
    retry = loop.client.post(
        confirmation_path, json=body, headers={"Idempotency-Key": request_key}
    )
    assert retry.status_code == 200, retry.text
    assert retry.json() == confirmed.json()
    position_id = confirmed.json()["position_id"]
    assert loop.position_ids() == [position_id]

    second = loop.submit(
        "请根据刚确认的岗位 JD 和 JR 给出招聘建议。", "confirmed-context"
    )
    second_answer = loop.drive_until_available(second)
    loop.wait_terminal(second)
    assert second_answer["answer"]["content"] == (controls / "answer-2.txt").read_text()
    assert second_answer["deliveries"] == []
    capture = json.loads((controls / "prompt-2.json").read_text())
    assert capture["model"] == "claude-opus-4-8"
    content = capture["prompt"]["message"]["content"]
    assert isinstance(content, str)
    # Parse the actual native-boundary prompt, not a separately rebuilt context.
    context = json.loads(content)
    position_context = context["hr_position_context"]
    assert position_context is not None
    position_context = json.loads(position_context)
    assert position_context["position_id"] == position_id
    assert (
        position_context["confirmed_context"]["version_id"]
        == confirmed.json()["context_version_id"]
    )
    assert (
        position_context["confirmed_context"]["modules"] == POSITION_PACKAGE["modules"]
    )
    assert loop.position_ids() == [position_id]
    assert loop.assistant_count(first) == loop.assistant_count(second) == 1
    with psycopg.connect(loop.environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.conversation_result_deliveries delivery join platform_control.conversation_messages message using(message_id) where message.conversation_id=%s",
            (loop.conversation_id,),
        ).fetchone() == (0,)

    resume_bytes = b"Synthetic resume: Candidate Alpha; six years of precision mechanical design and extrusion-system production.\n"
    distractor_bytes = b"INACTIVE_DISTRACTOR: unrelated accounting-only resume.\n"
    resume = loop.upload_resume("candidate-alpha.txt", resume_bytes)
    inactive = loop.upload_resume("inactive-resume.txt", distractor_bytes)
    expected_pdf = interview_pdf()
    (controls / "interview-source.pdf").write_bytes(expected_pdf)
    analysis = "候选人与已确认 JD/JR 的精密机械及量产要求匹配。面试题：请说明挤出系统量产经历，以及如何提升制造良率。PDF 单独准备。"
    (controls / "answer-3.txt").write_text(analysis)
    (controls / "fail-output").touch()
    third = loop.submit_with_resume(
        "请分析所选简历与本岗位的匹配情况，并生成面试题 PDF。", resume["attachment_id"]
    )
    original = loop.drive_until_available(third)
    assert original["answer"]["content"] == analysis
    assert original["result_enrichment"] == {
        "status": "pending",
        "pending_count": 1,
        "failed_count": 0,
    }
    assert loop.wait_terminal(third)["turn"]["status"] == "completed"
    wait_until(
        lambda: (controls / "output-failed").exists(),
        description="real output HTTP outage",
    )
    captured = json.loads((controls / "prompt-3.json").read_text())
    position_context = json.loads(
        json.loads(captured["prompt"]["message"]["content"])["hr_position_context"]
    )
    assert position_context["position_id"] == position_id
    assert (
        position_context["confirmed_context"]["modules"] == POSITION_PACKAGE["modules"]
    )
    files = captured["verifiedInputs"]
    assert [entry["attachmentId"] for entry in files] == [resume["attachment_id"]]
    assert files[0]["sha256"] == hashlib.sha256(resume_bytes).hexdigest()
    if bytes.fromhex(files[0]["bytesHex"]) != resume_bytes:
        raise AssertionError("Verified selected resume bytes differ")
    if inactive["attachment_id"] in json.dumps(
        captured
    ) or "INACTIVE_DISTRACTOR" in json.dumps(captured):
        raise AssertionError("Inactive attachment appeared in native input")
    # Actual coordinator/Result-consumer restart; the native execution remains
    # terminal and cannot be relaunched to recover this artifact.
    loop.restart_direct()
    (controls / "fail-output").unlink()
    ready = wait_until(
        lambda: (
            value
            if (value := loop.snapshot(third))["result_enrichment"]["status"] == "ready"
            else None
        ),
        timeout=40,
        description="real PDF processing and independent Result binding",
    )
    assert ready["answer"] == original["answer"]
    assert ready["turn"]["status"] == "completed"
    assert ready["deliveries"] == []
    messages = loop.client.get(
        f"/api/v1/conversations/{loop.conversation_id}/messages",
        params={"turn_id": third},
    ).json()["items"]
    answer_message = next(
        message for message in messages if message["role"] == "assistant"
    )
    assert len(answer_message["output_attachments"]) == 1
    output_id = answer_message["output_attachments"][0]["attachment_id"]
    downloaded = loop.download_artifact(output_id)
    assert downloaded == expected_pdf
    assert (
        hashlib.sha256(downloaded).hexdigest()
        == hashlib.sha256(expected_pdf).hexdigest()
    )
    assert (
        "Synthetic interview questions"
        in PdfReader(io.BytesIO(downloaded)).pages[0].extract_text()
    )
    metadata = loop.client.get(f"/api/v1/attachments/{output_id}").json()
    assert datetime.fromisoformat(metadata["retained_until"]) - datetime.fromisoformat(
        metadata["created_at"]
    ) >= timedelta(days=365)
    assert loop.assistant_count(third) == 1
    loop.assert_one_output_charge(third, output_id, len(expected_pdf))
