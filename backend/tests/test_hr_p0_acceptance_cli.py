from __future__ import annotations

import copy
import io
import json
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5

import pytest

from app.hr.p0_acceptance_cli import (
    AcceptanceConfig,
    AcceptanceFailure,
    PlatformP0AcceptanceGateway,
    build_gateway,
    load_config,
    main,
    run_controlled_acceptance,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "hr_p0"
RUN_ID = UUID("20000000-0000-4000-8000-000000000001")
CONVERSATIONS = tuple(
    UUID(f"30000000-0000-4000-8000-{index:012d}") for index in range(1, 6)
)
POSITION_ID = UUID("40000000-0000-4000-8000-000000000001")
CANDIDATES = (
    UUID("50000000-0000-4000-8000-000000000001"),
    UUID("50000000-0000-4000-8000-000000000002"),
)
POSITION_CANDIDATES = (
    UUID("60000000-0000-4000-8000-000000000001"),
    UUID("60000000-0000-4000-8000-000000000002"),
)
DOCUMENTS = (
    UUID("70000000-0000-4000-8000-000000000001"),
    UUID("70000000-0000-4000-8000-000000000002"),
)


def _fixture_result(filename: str, name: str) -> str:
    document = json.loads((FIXTURE_ROOT / filename).read_text("utf-8"))
    return next(
        item["markdown"] for item in document["results"] if item["name"] == name
    )


def _config_dict() -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent_id": "hr-bot",
        "api_base_url": "http://127.0.0.1:8080",
        "public_origin": "https://agent.orbbec.com.cn",
        "owner_id": "10000000-0000-4000-8000-000000000001",
        "session_cookie": "__Host-platform_session=synthetic-acceptance-cookie",
        "csrf_token": "synthetic-acceptance-csrf",
        "companies": [
            {
                "canonical_name": "示例光学科技甲",
                "aliases": [],
                "approved_urls": ["https://example.com/company-alpha"],
            },
            {
                "canonical_name": "示例智能制造乙",
                "aliases": [],
                "approved_urls": ["https://example.com/company-beta"],
            },
            {
                "canonical_name": "示例硬件系统丙",
                "aliases": [],
                "approved_urls": ["https://example.com/company-gamma"],
            },
        ],
        "connect_timeout_seconds": 3,
        "request_timeout_seconds": 20,
        "run_timeout_seconds": 900,
        "poll_interval_seconds": 2,
        "deployment_egress_evidence_sha256": "a" * 64,
    }


def _write_config(tmp_path: Path, value: object | None = None) -> Path:
    path = tmp_path / "hr-p0-acceptance.json"
    path.write_text(
        json.dumps(_config_dict() if value is None else value, ensure_ascii=False),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _turn(
    kind: str,
    markdown: str,
    *,
    urls: list[str] | None = None,
    **extra: object,
) -> dict[str, object]:
    return {
        "completed": True,
        "assistant_answer": markdown,
        "trace_answer": markdown,
        "envelope_kind": kind,
        "source_urls": urls or [],
        "progress_event_count": 1,
        **extra,
    }


def _evidence() -> dict[str, object]:
    panorama = _fixture_result("panorama-result.json", "partial_panorama_report")
    position = _fixture_result("recruiting-results.json", "revised_position_package")
    strong_match = _fixture_result("recruiting-results.json", "strong_candidate_match")
    adjacent_match = _fixture_result(
        "recruiting-results.json", "adjacent_candidate_match"
    )
    interview = _fixture_result(
        "recruiting-results.json", "strong_candidate_interview_plan"
    )
    return {
        "agent_id": "hr-bot",
        "business_delivery_calls": 0,
        "egress_evidence_sha256": "a" * 64,
        "turns": [
            _turn(
                "panorama_report",
                panorama,
                urls=[
                    "https://example.com/company-alpha/jobs/structure-001",
                    "https://example.com/company-beta/jobs/process-002",
                ],
            ),
            _turn("position_package", position),
            _turn(
                "panorama_retrieval",
                "已按点名公司读取最新人才全景证据。",
                urls=["https://example.com/company-alpha/jobs/structure-001"],
                retrieval={
                    "insight_version_ids": ["81000000-0000-4000-8000-000000000001"],
                    "source_id": "82000000-0000-4000-8000-000000000001",
                    "company": "示例光学科技甲",
                    "as_of": "2026-09-05T00:00:00+00:00",
                },
            ),
            _turn("position_package", position),
            _turn("candidate_match", strong_match),
            _turn("candidate_match", adjacent_match),
            _turn("candidate_interview_plan", interview),
        ],
        "artifact": {
            "state": "ready",
            "media_type": "application/pdf",
            "content": b"%PDF-1.7\nsynthetic acceptance\n%%EOF\n",
            "ticket_id": "80000000-0000-4000-8000-000000000001",
            "downloaded_ticket_id": "80000000-0000-4000-8000-000000000001",
            "fresh_ticket": True,
        },
        "created_ids": {
            "conversation_ids": [str(value) for value in CONVERSATIONS],
            "position_ids": [str(POSITION_ID)],
            "candidate_ids": [str(value) for value in CANDIDATES],
            "position_candidate_ids": [str(value) for value in POSITION_CANDIDATES],
            "candidate_document_ids": [str(value) for value in DOCUMENTS],
        },
    }


class FakeGateway:
    def __init__(self, evidence: dict[str, object] | None = None) -> None:
        self.evidence = evidence or _evidence()
        self.created_ids = copy.deepcopy(self.evidence["created_ids"])
        self.archived: list[tuple[dict[str, tuple[UUID, ...]], float]] = []
        self.execute_deadline: float | None = None
        self.raise_execute: AcceptanceFailure | None = None
        self.raise_archive = False

    def execute(
        self,
        config: AcceptanceConfig,
        *,
        run_id: UUID,
        fixture_root: Path,
        deadline: float,
    ) -> dict[str, object]:
        assert config.agent_id == "hr-bot"
        assert run_id == RUN_ID
        assert fixture_root == FIXTURE_ROOT
        self.execute_deadline = deadline
        if self.raise_execute is not None:
            raise self.raise_execute
        return copy.deepcopy(self.evidence)

    def archive_exact(
        self,
        config: AcceptanceConfig,
        created_ids: dict[str, tuple[UUID, ...]],
        *,
        deadline: float,
    ) -> None:
        assert config.agent_id == "hr-bot"
        self.archived.append((created_ids, deadline))
        if self.raise_archive:
            raise RuntimeError("sensitive cleanup detail")


def test_valid_config_prints_exact_status_only_line_and_archives_exact_ids(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)
    gateway = FakeGateway()
    stdout = io.StringIO()
    stderr = io.StringIO()

    result = main(
        [],
        config_path=config_path,
        fixture_root=FIXTURE_ROOT,
        gateway_factory=lambda _config: gateway,
        uuid_factory=lambda: RUN_ID,
        monotonic=lambda: 100.0,
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 0
    assert stdout.getvalue() == f"HR_P0_ACCEPTANCE_OK {RUN_ID}\n"
    assert stderr.getvalue() == ""
    assert "候选人" not in stdout.getvalue()
    assert "https://" not in stdout.getvalue()
    assert gateway.execute_deadline == 980.0
    assert len(gateway.archived) == 1
    archived, cleanup_deadline = gateway.archived[0]
    assert archived == {
        "conversation_ids": CONVERSATIONS,
        "position_ids": (POSITION_ID,),
        "candidate_ids": CANDIDATES,
        "position_candidate_ids": POSITION_CANDIDATES,
        "candidate_document_ids": DOCUMENTS,
    }
    assert cleanup_deadline == 1000.0


def test_default_gateway_is_concrete_fixed_platform_api_wiring(tmp_path: Path) -> None:
    config = load_config(
        _write_config(tmp_path), expected_path=tmp_path / "hr-p0-acceptance.json"
    )
    gateway = build_gateway(config)

    assert type(gateway) is PlatformP0AcceptanceGateway
    assert gateway.created_ids == {
        "conversation_ids": [],
        "position_ids": [],
        "candidate_ids": [],
        "position_candidate_ids": [],
        "candidate_document_ids": [],
    }


@pytest.mark.parametrize("failed_task", [None, "match:0", "match:1", "interview"])
def test_concrete_gateway_drives_public_flow_and_writes_exact_cleanup_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_task: str | None
) -> None:
    def uid(label: str) -> str:
        return str(uuid5(RUN_ID, label))

    answers = {
        uid("conversation:panorama"): _fixture_result(
            "panorama-result.json", "partial_panorama_report"
        ),
        uid("conversation:position"): _fixture_result(
            "recruiting-results.json", "initial_position_package"
        ),
        uid("conversation:match:0"): _fixture_result(
            "recruiting-results.json", "strong_candidate_match"
        ),
        uid("conversation:match:1"): _fixture_result(
            "recruiting-results.json", "adjacent_candidate_match"
        ),
        uid("conversation:interview"): _fixture_result(
            "recruiting-results.json", "strong_candidate_interview_plan"
        ),
    }
    position_answers = {
        uid("turn:position"): answers[uid("conversation:position")],
        uid("turn:position-retrieval"): "已读取示例光学科技甲最新人才全景证据。",
        uid("turn:position-revision"): _fixture_result(
            "recruiting-results.json", "revised_position_package"
        ),
    }
    run_by_turn = {
        turn_id: uid(f"run:{turn_id}")
        for turn_id in (
            uid("turn:panorama"),
            *position_answers,
            uid("turn:match:0"),
            uid("turn:match:1"),
            uid("turn:interview"),
        )
    }
    conversation_by_turn = {
        uid("turn:panorama"): uid("conversation:panorama"),
        **{turn_id: uid("conversation:position") for turn_id in position_answers},
        uid("turn:match:0"): uid("conversation:match:0"),
        uid("turn:match:1"): uid("conversation:match:1"),
        uid("turn:interview"): uid("conversation:interview"),
    }

    class Response:
        def __init__(self, value=None, *, status=200, content=b"") -> None:
            self.status_code = status
            self.value = value
            self.content = content

        def json(self):
            return copy.deepcopy(self.value)

    class Cookies:
        def __init__(self, initial) -> None:
            self.values = dict(initial)

        def set(self, name, value) -> None:
            self.values[name] = value

    class Client:
        def __init__(self, **kwargs) -> None:
            assert kwargs["base_url"] == "http://127.0.0.1:8080"
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
            self.cookies = Cookies(kwargs["cookies"])
            self.calls: list[tuple[str, str]] = []
            self.source_count = 0
            self.upload_count = 0
            self.confirm_count = 0
            self.task_count = 0
            self.ticket_count = 0
            self.append_count = 0
            self.package_count = 0
            self.context_draft_count = 0
            self.flywheel_answer = ""
            self.flywheel_turn_id = ""
            self.revised_modules = None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def request(self, method, path, **kwargs):
            self.calls.append((method, path))
            assert "timeout" not in kwargs
            if method not in {"GET", "HEAD"}:
                assert kwargs["headers"]["Origin"] == "https://agent.orbbec.com.cn"
                assert kwargs["headers"]["X-CSRF-Token"] == "synthetic-acceptance-csrf"
            if path == "/api/v1/account":
                return Response(
                    {
                        "internal_user_id": _config_dict()["owner_id"],
                        "csrf_token": "synthetic-acceptance-csrf",
                    }
                )
            if method == "GET" and path == "/api/hr/panorama/sources":
                company = _config_dict()["companies"][0]
                return Response(
                    {
                        "items": [
                            {
                                **company,
                                "source_id": uid("source:existing"),
                                "active": True,
                            }
                        ]
                    }
                )
            if method == "POST" and path == "/api/hr/panorama/sources":
                self.source_count += 1
                return Response(
                    {
                        **kwargs["json"],
                        "source_id": uid(f"source:{self.source_count}"),
                    }
                )
            if method == "POST" and path == "/api/hr/panorama/runs":
                return Response(
                    {
                        "run_id": uid("panorama-run"),
                        "conversation_id": uid("conversation:panorama"),
                    },
                    status=202,
                )
            if path == f"/api/hr/panorama/runs/{uid('panorama-run')}":
                return Response({"state": "partially_completed"})
            if path == "/api/hr/panorama/reports":
                return Response(
                    {
                        "items": [
                            {
                                "run_id": uid("panorama-run"),
                                "insight_version_id": uid("insight"),
                            }
                        ]
                    }
                )
            if path == f"/api/hr/panorama/reports/{uid('insight')}":
                return Response(
                    {
                        "insight": {
                            "source_conversation_id": uid("conversation:panorama"),
                            "source_turn_id": uid("turn:panorama"),
                            "facts": [
                                {
                                    "source_url": "https://example.com/company-alpha/jobs/1",
                                    "observed_at": "2026-09-05T00:00:00+00:00",
                                },
                                {
                                    "source_url": "https://example.com/company-beta/jobs/2",
                                    "observed_at": "2026-09-04T00:00:00+00:00",
                                },
                            ],
                        },
                        "snapshots": [
                            {
                                "source_id": uid("source:existing"),
                                "source_url": "https://example.com/company-alpha/jobs/1",
                                "observed_at": "2026-09-05T00:00:00+00:00",
                            },
                            {
                                "source_id": uid("source:1"),
                                "source_url": "https://example.com/company-beta/jobs/2",
                                "observed_at": "2026-09-04T00:00:00+00:00",
                            },
                        ],
                    }
                )
            if method == "POST" and path == "/api/v1/agents/hr-bot/conversations":
                return Response(
                    {
                        "conversation": {
                            "conversation_id": uid("conversation:position")
                        },
                        "turn": {"turn_id": uid("turn:position")},
                    },
                    status=201,
                )
            if (
                method == "POST"
                and path
                == f"/api/v1/conversations/{uid('conversation:position')}/messages"
            ):
                self.append_count += 1
                name = (
                    "position-retrieval"
                    if self.append_count == 1
                    else "position-revision"
                )
                text = kwargs["json"]["text"]
                assert "示例光学科技甲" in text
                if self.append_count == 1:
                    assert "不要生成或修改岗位草案" in text
                    assert "position_package" not in text
                else:
                    assert "JD 和 JR" in text
                    assert "position_package" in text
                return Response({"turn": {"turn_id": uid(f"turn:{name}")}}, status=201)
            if (
                method == "GET"
                and path.startswith("/api/v1/conversations/")
                and path.endswith("/messages")
            ):
                conversation = path.split("/")[4]
                name = next(
                    value
                    for value in (
                        "panorama",
                        "position",
                        "match:0",
                        "match:1",
                        "interview",
                    )
                    if conversation == uid(f"conversation:{value}")
                )
                artifacts = []
                if name == "interview":
                    artifacts = [
                        {
                            "status": "ready",
                            "attachment": {
                                "attachment_id": uid("pdf"),
                                "detected_mime": "application/pdf",
                            },
                        }
                    ]
                items = [
                    {
                        "role": "assistant",
                        "turn_id": uid(f"turn:{name}"),
                        "content": answers[conversation],
                        "citations": [],
                        "artifact_versions": artifacts,
                    }
                ]
                if name == "position":
                    items = [
                        {
                            "role": "assistant",
                            "turn_id": turn_id,
                            "content": answer,
                            "citations": [],
                            "artifact_versions": [],
                        }
                        for turn_id, answer in list(position_answers.items())[
                            : self.append_count + 1
                        ]
                    ]
                return Response({"items": items})
            if method == "GET" and path.startswith("/api/v1/conversations/"):
                conversation = path.split("/")[4]
                name = next(
                    value
                    for value in (
                        "panorama",
                        "position",
                        "match:0",
                        "match:1",
                        "interview",
                    )
                    if conversation == uid(f"conversation:{value}")
                )
                current_turn_id = uid(f"turn:{name}")
                if name == "position":
                    current_turn_id = list(position_answers)[self.append_count]
                return Response(
                    {
                        "current_turn": {
                            "turn_id": current_turn_id,
                            "status": "completed",
                        }
                    }
                )
            if path == "/api/sessions":
                query = kwargs["params"]["q"]
                assert kwargs["params"]["source_kind"] == "metabot"
                assert kwargs["params"]["date_from"].endswith("+00:00")
                candidates = {
                    uid("turn:panorama"): answers[uid("conversation:panorama")],
                    **position_answers,
                    uid("turn:match:0"): answers[uid("conversation:match:0")],
                    uid("turn:match:1"): answers[uid("conversation:match:1")],
                    uid("turn:interview"): answers[uid("conversation:interview")],
                }
                self.flywheel_turn_id, self.flywheel_answer = next(
                    (turn_id, answer)
                    for turn_id, answer in candidates.items()
                    if answer.startswith(query)
                )
                return Response({"items": [{"session_key": "metabot:session"}]})
            if path == "/api/sessions/metabot:session":
                expected_trace = f"metabot:hr-bot:{run_by_turn[self.flywheel_turn_id]}"
                return Response(
                    {
                        "turns": [
                            {
                                "answer": self.flywheel_answer,
                                "trace_key": "metabot:hr-bot:historical",
                                "turn_key": "metabot:hr-bot:historical-turn",
                                "created_at": "2026-09-04T00:00:00+00:00",
                            },
                            {
                                "answer": self.flywheel_answer,
                                "trace_key": expected_trace,
                                "turn_key": f"metabot:hr-bot:{self.flywheel_turn_id}",
                                "created_at": "2026-09-05T00:00:01+00:00",
                            },
                        ]
                    }
                )
            if path == "/api/turns/metabot:hr-bot:historical-turn/trace":
                return Response(
                    {
                        "trace_key": "metabot:hr-bot:historical",
                        "steps": [{"name": "send_business_message"}],
                    }
                )
            if path == f"/api/turns/metabot:hr-bot:{self.flywheel_turn_id}/trace":
                return Response(
                    {
                        "trace_key": f"metabot:hr-bot:{run_by_turn[self.flywheel_turn_id]}",
                        "turn_key": f"metabot:hr-bot:{self.flywheel_turn_id}",
                        "status": "completed",
                        "started_at": "2026-09-05T00:00:00+00:00",
                        "completed_at": "2026-09-05T00:00:02+00:00",
                        "steps": [],
                    }
                )
            if path.endswith("/position-package"):
                self.package_count += 1
                return Response(
                    {
                        "draft_id": uid("position-draft"),
                        "draft_version_id": uid("position-version:1"),
                        "conversation_id": uid("conversation:position"),
                        "version_number": 1,
                        "modules": {
                            "mission": {"text": "构建合成团队"},
                            "jd": {"text": "JD version 1"},
                            "jr": {"text": "JR version 1"},
                        },
                        "row_version": 1,
                    }
                )
            if "/position-drafts/" in path and path.endswith("/confirm"):
                assert uid("position-version:1") in path
                return Response(
                    {
                        "position_id": uid("position"),
                        "context_version_id": uid("context:1"),
                        "conversation_id": uid("conversation:position"),
                    }
                )
            if method == "POST" and path.endswith("/context/drafts"):
                self.context_draft_count += 1
                assert kwargs["json"]["base_context_version_id"] == uid("context:1")
                assert kwargs["json"]["source_turn_id"] == uid("turn:position-revision")
                assert kwargs["json"]["modules"]["jd"] != {"text": "JD version 1"}
                self.revised_modules = kwargs["json"]["modules"]
                return Response(
                    {
                        "context_version_id": uid("context:2-draft"),
                        "row_version": 1,
                        "modules": kwargs["json"]["modules"],
                    }
                )
            if (
                method == "POST"
                and "/context/drafts/" in path
                and path.endswith("/confirm")
            ):
                assert uid("context:2-draft") in path
                return Response(
                    {
                        "context_version_id": uid("context:2"),
                        "version_number": 2,
                        "state": "confirmed",
                        "modules": self.revised_modules,
                    }
                )
            if method == "POST" and path == "/api/v1/attachments/uploads":
                self.upload_count += 1
                return Response(
                    {"upload_id": uid(f"upload:{self.upload_count}")}, status=201
                )
            if method == "PUT" and "/attachments/uploads/" in path:
                return Response({"state": "uploaded"})
            if method == "POST" and path.endswith("/complete"):
                upload = path.split("/")[-2]
                number = next(
                    value for value in range(1, 4) if upload == uid(f"upload:{value}")
                )
                return Response({"attachment_id": uid(f"attachment:{number}")})
            if method == "POST" and path.endswith("/candidate-drafts:batch"):
                return Response(
                    {
                        "items": [
                            {"draft_id": uid(f"draft:{value}")} for value in range(3)
                        ]
                    },
                    status=202,
                )
            if method == "GET" and "/candidate-drafts/" in path:
                number = next(
                    value for value in range(3) if path.endswith(uid(f"draft:{value}"))
                )
                return Response(
                    {
                        "draft_id": uid(f"draft:{number}"),
                        "state": "ready" if number < 2 else "failed",
                        "row_version": 1,
                        "extracted_facts": {"stable_name": f"合成人选{number}"},
                    }
                )
            if method == "POST" and path.endswith(":retry"):
                return Response({"draft_id": uid("draft:2")})
            if method == "POST" and path.endswith(":dismiss"):
                return Response({"draft_id": uid("draft:2"), "state": "dismissed"})
            if method == "POST" and path.endswith(":confirm"):
                number = self.confirm_count
                self.confirm_count += 1
                return Response(
                    {
                        "candidate": {"candidate_id": uid(f"candidate:{number}")},
                        "document": {"document_id": uid(f"document:{number}")},
                        "position_candidate": {
                            "candidate_id": uid(f"candidate:{number}"),
                            "position_candidate_id": uid(f"relation:{number}"),
                        },
                    },
                    status=201,
                )
            if method == "POST" and path.endswith("/tasks"):
                name = (
                    f"match:{self.task_count}" if self.task_count < 2 else "interview"
                )
                self.task_count += 1
                return Response(
                    {
                        "task_id": uid(f"task:{name}"),
                        "conversation_id": uid(f"conversation:{name}"),
                        "turn_id": uid(f"turn:{name}"),
                    },
                    status=202,
                )
            if method == "GET" and "/tasks/" in path:
                task_id = path.rsplit("/", 1)[1]
                name = next(
                    value
                    for value in ("match:0", "match:1", "interview")
                    if task_id == uid(f"task:{value}")
                )
                if name == failed_task:
                    return Response({"status": "failed"})
                return Response(
                    {
                        "status": "completed",
                        "conversation_id": uid(f"conversation:{name}"),
                        "turn_id": uid(f"turn:{name}"),
                    }
                )
            if method == "GET" and path.endswith("/analyses"):
                number = next(
                    value for value in range(2) if uid(f"relation:{value}") in path
                )
                items = [
                    {
                        "analysis_kind": "match",
                        "candidate_id": uid(f"candidate:{number}"),
                        "context_version_id": uid("context:2"),
                    }
                ]
                if number == 0:
                    items.append(
                        {
                            "analysis_kind": "candidate_interview_plan",
                            "source_artifact_version_id": uid("artifact-version"),
                        }
                    )
                return Response({"items": items})
            if method == "GET" and path.endswith("/resources"):
                return Response(
                    {
                        "artifacts": [
                            {
                                "attachment_id": uid("pdf"),
                                "artifact_version_id": uid("artifact-version"),
                                "state": "ready",
                                "media_type": "application/pdf",
                                "download_available": True,
                            }
                        ]
                    }
                )
            if method == "POST" and path.endswith("/ticket"):
                self.ticket_count += 1
                return Response(
                    {
                        "content_path": (
                            f"/api/v1/attachments/content/ticket-{self.ticket_count}"
                        )
                    }
                )
            if method == "GET" and path.startswith("/api/v1/attachments/content/"):
                return Response(content=b"%PDF-1.7\nscripted\n%%EOF\n")
            if method == "POST" and path.endswith("/archive"):
                return Response({"status": "archived"})
            raise AssertionError(f"unexpected request {method} {path}")

    client = Client(
        base_url="http://127.0.0.1:8080",
        cookies={},
        follow_redirects=False,
        trust_env=False,
    )
    fake_httpx = SimpleNamespace(
        Client=lambda **_kwargs: client,
        Timeout=lambda *_args, **_kwargs: object(),
    )

    class DbResult:
        def __init__(self, value) -> None:
            self.value = value

        def fetchone(self):
            return self.value

    class DbConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def execute(self, statement, params):
            if "platform_hr.position_insight_retrievals" in statement:
                assert params == (
                    UUID(_config_dict()["owner_id"]),
                    UUID(uid("position")),
                    UUID(uid("conversation:position")),
                    UUID(uid("turn:position-retrieval")),
                )
                return DbResult(
                    (
                        [UUID(uid("insight"))],
                        [
                            {
                                "insight_version_ids": [uid("insight")],
                                "freshness": {
                                    "as_of": "2026-09-05T00:00:00+00:00",
                                    "status": "current",
                                },
                                "facts": [
                                    {
                                        "source_url": "https://example.com/company-alpha/jobs/1",
                                        "observed_at": "2026-09-05T00:00:00+00:00",
                                    }
                                ],
                                "source_urls": [
                                    "https://example.com/company-alpha/jobs/1"
                                ],
                            }
                        ],
                    )
                )
            if "platform_control.mission_runs" in statement:
                turn_id = str(params[2])
                assert params[0] == UUID(_config_dict()["owner_id"])
                assert str(params[1]) == conversation_by_turn[turn_id]
                return DbResult(
                    (
                        UUID(run_by_turn[turn_id]),
                        datetime(2026, 9, 5, tzinfo=timezone.utc),
                    )
                )
            return DbResult((0,) if "agent_action_deliveries" in statement else (1,))

    import app.config as platform_config
    from app import local_secrets

    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=lambda *_args, **_kwargs: DbConnection()),
    )
    monkeypatch.setattr(
        platform_config,
        "load_config",
        lambda: SimpleNamespace(
            control_plane=SimpleNamespace(control_database_url_file="/fixed/db-url")
        ),
    )
    monkeypatch.setattr(local_secrets, "read_secret_file", lambda _path: "fixed-dsn")
    config_path = _write_config(tmp_path)
    config = load_config(config_path, expected_path=config_path)
    cleanup_root = tmp_path / "cleanup"
    cleanup_root.mkdir(mode=0o700)
    gateway = PlatformP0AcceptanceGateway(
        cleanup_manifest_path=cleanup_root / "cleanup.json"
    )

    if failed_task is None:
        run_id = run_controlled_acceptance(
            config,
            gateway,
            fixture_root=FIXTURE_ROOT,
            uuid_factory=lambda: RUN_ID,
        )
        assert run_id == RUN_ID
    else:
        with pytest.raises(AcceptanceFailure, match="^TURN_FAILED$"):
            run_controlled_acceptance(
                config,
                gateway,
                fixture_root=FIXTURE_ROOT,
                uuid_factory=lambda: RUN_ID,
            )

    assert client.source_count == 2
    assert client.upload_count == 3
    assert client.confirm_count == 2
    assert client.append_count == 2
    assert client.package_count == 1
    assert client.context_draft_count == 1
    assert (
        "GET",
        "/api/turns/metabot:hr-bot:historical-turn/trace",
    ) not in client.calls
    exact_trace_count = sum(
        method == "GET"
        and path.startswith("/api/turns/metabot:hr-bot:")
        and path.endswith("/trace")
        for method, path in client.calls
    )
    assert (
        exact_trace_count
        == {
            None: 7,
            "match:0": 4,
            "match:1": 5,
            "interview": 6,
        }[failed_task]
    )
    expected_task_count = {None: 3, "match:0": 1, "match:1": 2, "interview": 3}
    expected_conversation_count = {
        None: 5,
        "match:0": 3,
        "match:1": 4,
        "interview": 5,
    }
    assert client.task_count == expected_task_count[failed_task]
    assert client.ticket_count == (2 if failed_task is None else 0)
    assert sum(path.endswith(":retry") for _, path in client.calls) == 1
    assert sum(path.endswith(":dismiss") for _, path in client.calls) == 1
    assert (
        sum(path.endswith("/archive") for _, path in client.calls)
        == (expected_conversation_count[failed_task])
    )
    manifest = json.loads((cleanup_root / "cleanup.json").read_text("utf-8"))
    assert stat.S_IMODE((cleanup_root / "cleanup.json").stat().st_mode) == 0o600
    assert manifest["schema_version"] == 1
    assert manifest["owner_id"] == _config_dict()["owner_id"]
    assert manifest["created_ids"] == gateway.created_ids
    assert (
        len(manifest["created_ids"]["conversation_ids"])
        == (expected_conversation_count[failed_task])
    )
    if failed_task is None:
        package_path = (
            f"/api/hr/conversations/{uid('conversation:position')}/position-package"
        )
        message_path = f"/api/v1/conversations/{uid('conversation:position')}/messages"
        context_draft_path = f"/api/hr/positions/{uid('position')}/context/drafts"
        package_index = client.calls.index(("GET", package_path))
        first_message_index = client.calls.index(("POST", message_path))
        second_message_index = client.calls.index(
            ("POST", message_path), first_message_index + 1
        )
        context_draft_index = client.calls.index(("POST", context_draft_path))
        assert package_index < first_message_index
        assert first_message_index < second_message_index < context_draft_index


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda value: value["turns"][0].update(assistant_answer=""),
            "EMPTY_ASSISTANT",
        ),
        (lambda value: value["turns"][0].update(trace_answer=""), "EMPTY_TRACE"),
        (
            lambda value: value["turns"][1].update(
                assistant_answer="# visible\n<!-- platform-hr-v1:invalid -->"
            ),
            "INVALID_ENVELOPE",
        ),
        (
            lambda value: value["turns"][0].update(
                source_urls=["https://unapproved.example.net/jobs/1"]
            ),
            "SOURCE_SCOPE",
        ),
        (lambda value: value.update(agent_id="marketing-bot"), "WRONG_AGENT"),
        (
            lambda value: value["artifact"].update(state="processing"),
            "ARTIFACT_NOT_READY",
        ),
        (
            lambda value: value["artifact"].update(content=b"not a pdf"),
            "ARTIFACT_NOT_PDF",
        ),
        (
            lambda value: value["artifact"].update(fresh_ticket=False),
            "ARTIFACT_TICKET",
        ),
        (
            lambda value: value.update(business_delivery_calls=1),
            "BUSINESS_DELIVERY",
        ),
        (
            lambda value: value.update(egress_evidence_sha256="b" * 64),
            "EGRESS_EVIDENCE",
        ),
    ],
)
def test_evidence_failures_are_sanitized_and_still_archive_exact_ids(
    tmp_path: Path, mutation, code: str
) -> None:
    config = load_config(
        _write_config(tmp_path), expected_path=tmp_path / "hr-p0-acceptance.json"
    )
    evidence = _evidence()
    mutation(evidence)
    gateway = FakeGateway(evidence)

    with pytest.raises(AcceptanceFailure, match=f"^{code}$"):
        run_controlled_acceptance(
            config,
            gateway,
            fixture_root=FIXTURE_ROOT,
            uuid_factory=lambda: RUN_ID,
            monotonic=lambda: 100.0,
        )

    assert len(gateway.archived) == 1


def test_timeout_uses_bounded_deadline_and_best_effort_exact_cleanup(
    tmp_path: Path,
) -> None:
    config = load_config(
        _write_config(tmp_path), expected_path=tmp_path / "hr-p0-acceptance.json"
    )
    gateway = FakeGateway()
    gateway.raise_execute = AcceptanceFailure("TIMEOUT")
    gateway.raise_archive = True

    with pytest.raises(AcceptanceFailure, match="^TIMEOUT$"):
        run_controlled_acceptance(
            config,
            gateway,
            fixture_root=FIXTURE_ROOT,
            uuid_factory=lambda: RUN_ID,
            monotonic=lambda: 100.0,
        )

    assert gateway.execute_deadline == 980.0
    assert len(gateway.archived) == 1


@pytest.mark.parametrize("kind", ["malformed", "oversized", "symlink", "wrong_mode"])
def test_config_rejects_malformed_oversized_symlink_and_wrong_mode(
    tmp_path: Path, kind: str
) -> None:
    path = _write_config(tmp_path)
    expected = path
    if kind == "malformed":
        path.write_text("{", encoding="utf-8")
    elif kind == "oversized":
        path.write_bytes(b"{" + b" " * 65_536 + b"}")
    elif kind == "symlink":
        target = tmp_path / "real.json"
        path.replace(target)
        path.symlink_to(target)
    else:
        path.chmod(0o640)

    with pytest.raises(AcceptanceFailure, match="^CONFIG_INVALID$"):
        load_config(path, expected_path=expected)


def test_config_rejects_wrong_target_and_noncanonical_path(tmp_path: Path) -> None:
    value = _config_dict()
    value["agent_id"] = "marketing-bot"
    path = _write_config(tmp_path, value)
    with pytest.raises(AcceptanceFailure, match="^CONFIG_INVALID$"):
        load_config(path, expected_path=path)


def test_config_rejects_run_timeout_without_request_headroom(tmp_path: Path) -> None:
    value = _config_dict()
    value["request_timeout_seconds"] = 60
    value["run_timeout_seconds"] = 60
    path = _write_config(tmp_path, value)

    with pytest.raises(AcceptanceFailure, match="^CONFIG_INVALID$"):
        load_config(path, expected_path=path)

    alias = tmp_path / "nested" / ".." / path.name
    with pytest.raises(AcceptanceFailure, match="^CONFIG_INVALID$"):
        load_config(alias, expected_path=path)


def test_main_failure_is_one_sanitized_code_without_gateway_detail(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway()
    gateway.raise_execute = AcceptanceFailure("TIMEOUT")
    stdout = io.StringIO()
    stderr = io.StringIO()

    result = main(
        [],
        config_path=_write_config(tmp_path),
        fixture_root=FIXTURE_ROOT,
        gateway_factory=lambda _config: gateway,
        uuid_factory=lambda: RUN_ID,
        monotonic=lambda: 100.0,
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "HR_P0_ACCEPTANCE_FAILED TIMEOUT\n"
    for forbidden in (
        "sensitive cleanup detail",
        "synthetic-acceptance-cookie",
        "候选人",
        "https://",
    ):
        assert forbidden not in stderr.getvalue()


def test_failure_type_cannot_inject_arbitrary_status_detail() -> None:
    error = AcceptanceFailure("secret https://example.com candidate")

    assert str(error) == "INTERNAL"


def test_fixture_files_are_exact_task1_synthetic_inputs() -> None:
    expected = {
        "panorama-result.json",
        "recruiting-results.json",
        "resume-adjacent.md",
        "resume-invalid.txt",
        "resume-strong.md",
    }
    assert {item.name for item in FIXTURE_ROOT.iterdir()} == expected
    for item in FIXTURE_ROOT.iterdir():
        assert "SYNTHETIC TEST DATA" in item.read_text("utf-8")
        assert stat.S_ISREG(item.stat().st_mode)
