from datetime import UTC, datetime, timedelta
from copy import deepcopy
import hashlib
import json

import pytest

from app.cloud_replica.crypto import FieldCipher
from app.cloud_replica.repository import ReplicaObservabilityRepository
from app.observability.models import SessionFilters
from app.observability.repository import ObservabilityReadError


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.lower().split())
        self.connection.statements.append((normalized, params))
        if "to_regclass" in normalized:
            self.rows = [{"sessions": "platform_replica.sessions", "generations": "platform_replica.generations"}]
        elif "max(committed_at)" in normalized:
            self.rows = [{"last_success_at": self.connection.last_success_at}]
        elif "from platform_replica.sessions" in normalized:
            rows = self.connection.rows
            if "where session_key = %s" in normalized:
                rows = [row for row in rows if row["session_key"] == params[0]]
            self.rows = rows
        else:
            self.rows = []
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Connection:
    def __init__(self, rows, last_success_at):
        self.rows = rows
        self.last_success_at = last_success_at
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _Cursor(self)


def _record(
    now,
    key="a" * 52,
    title="人才定位",
    question="寻找视觉算法人才",
    *,
    agent_id="hr-bot",
    turn_key="c" * 52,
):
    return {
        "kind": "session",
        "key": key,
        "user_id": "b" * 52,
        "agent_id": agent_id,
        "source_kind": "metabot",
        "channel": "feishu",
        "title": {"text": title, "safe": True, "sha256": "1" * 64, "policy_version": "v1"},
        "primary_sender_name": "磐德",
        "primary_sender_department": "HR",
        "created_at": (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "last_active_at": now.isoformat().replace("+00:00", "Z"),
        "turns": [
            {
                "key": turn_key,
                "turn_index": 1,
                "question": {"text": question, "safe": True, "sha256": "2" * 64, "policy_version": "v1"},
                "answer": {"text": "建议优先搜索 GitHub", "safe": True, "sha256": "3" * 64, "policy_version": "v1"},
                "created_at": now.isoformat().replace("+00:00", "Z"),
                "outcome": "success",
                "fallback_used": False,
                "duration_ms": 1200,
                "attachments": [
                    {
                        "display_label": "附件 1",
                        "category": "document",
                        "mime_type": "application/pdf",
                        "size_bucket": "100 KiB–1 MiB",
                        "direction": "generated",
                        "archive_status": "archived",
                        "delivery_status": "delivered",
                        "occurred_at": now.isoformat().replace("+00:00", "Z"),
                    }
                ],
                "trace": {
                    "status": "success", "duration_ms": 1200, "engine": "claude-code",
                    "backend": "anthropic", "model_family": "claude", "input_tokens": 10,
                    "output_tokens": 20, "cost_usd": 0.1, "error_class": None,
                    "tool_categories": ["web_search"],
                },
            }
        ],
        "sanitizer_policy_version": "v1",
    }


def _row(cipher, record, committed_sequence=1):
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encrypted = cipher.encrypt(canonical, f"1:session:{record['key']}")
    import base64

    decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    return {
        "session_key": record["key"],
        "agent_id": record["agent_id"],
        "source_kind": record["source_kind"],
        "channel": record["channel"],
        "created_at": datetime.fromisoformat(record["created_at"].replace("Z", "+00:00")),
        "last_active_at": datetime.fromisoformat(record["last_active_at"].replace("Z", "+00:00")),
        "generation_sequence": committed_sequence,
        "display_payload": decode(encrypted["ciphertext"]),
        "payload_nonce": decode(encrypted["nonce"]),
        "payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
    }


def _repository(now, records):
    cipher = FieldCipher(b"e" * 32)
    connection = _Connection([_row(cipher, record) for record in records], now)
    return ReplicaObservabilityRepository(
        "postgresql://replica",
        cipher=cipher,
        connect=lambda *_args, **_kwargs: connection,
        now=lambda: now,
        stale_seconds=900,
    ), connection


def test_repository_lists_searches_paginates_and_returns_existing_shapes():
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    repository, _ = _repository(
        now,
        (
            _record(now, "a" * 52, "人才定位", "寻找视觉算法人才"),
            _record(now - timedelta(minutes=1), "d" * 52, "面试方案", "设计面试题"),
        ),
    )

    page = repository.list_sessions(
        SessionFilters(query="视觉"), limit=1, offset=0
    )
    detail = repository.get_session("a" * 52)

    assert page.total == 1
    assert page.items[0].title == "人才定位"
    assert detail is not None
    assert detail.primary_sender_name == "磐德"
    assert detail.turns[0].question == "寻找视觉算法人才"
    assert detail.turns[0].answer == "建议优先搜索 GitHub"
    attachment = detail.turns[0].output_attachments[0]
    assert attachment.display_name == "附件 1"
    assert attachment.size_bucket == "100 KiB–1 MiB"
    assert attachment.content_available is False
    assert detail.turns[0].evidence_availability == "restricted"


def test_repository_returns_aggregate_trace_without_raw_steps():
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    repository, _ = _repository(now, (_record(now),))

    trace = repository.get_trace("c" * 52)

    assert trace is not None
    assert trace.model == "claude"
    assert trace.input_tokens == 10
    assert trace.detail_availability == "unavailable"
    assert trace.steps == []
    assert trace.error_message is None


def test_repository_freshness_supports_empty_current_stale_and_unavailable():
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    repository, connection = _repository(now, ())

    assert repository.deployment_status()["freshness"] == "current"
    connection.last_success_at = now - timedelta(seconds=901)
    assert repository.deployment_status()["freshness"] == "stale"
    connection.last_success_at = None
    assert repository.deployment_status()["freshness"] == "unavailable"
    assert repository.list_sessions(SessionFilters(), 50, 0).items == []


def test_repository_reports_configured_public_authentication_mode():
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    cipher = FieldCipher(b"e" * 32)
    connection = _Connection([], now)
    repository = ReplicaObservabilityRepository(
        "postgresql://replica",
        cipher=cipher,
        connect=lambda *_args, **_kwargs: connection,
        now=lambda: now,
        auth_mode="basic-auth",
    )

    assert repository.deployment_status()["auth"] == "basic-auth"


def test_wrong_key_or_corrupt_ciphertext_fails_without_partial_data():
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    good_cipher = FieldCipher(b"e" * 32)
    connection = _Connection([_row(good_cipher, _record(now))], now)
    repository = ReplicaObservabilityRepository(
        "postgresql://replica",
        cipher=FieldCipher(b"x" * 32),
        connect=lambda *_args, **_kwargs: connection,
        now=lambda: now,
    )

    with pytest.raises(ObservabilityReadError):
        repository.list_sessions(SessionFilters(), 50, 0)


def test_repository_rejects_limit_above_ui_contract():
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    repository, _ = _repository(now, ())

    with pytest.raises(ObservabilityReadError):
        repository.list_sessions(SessionFilters(), 101, 0)


def test_repository_excludes_platform_hidden_agents_from_all_read_paths():
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    fae_session_key = "f" * 52
    fae_turn_key = "1" * 52
    codex_session_key = "i" * 52
    repository, _ = _repository(
        now,
        (
            _record(now, "h" * 52, agent_id="hr-bot", turn_key="2" * 52),
            _record(
                now,
                fae_session_key,
                agent_id="fae-bot",
                turn_key=fae_turn_key,
            ),
            _record(
                now,
                codex_session_key,
                agent_id="codex-assistant",
                turn_key="3" * 52,
            ),
        ),
    )

    page = repository.list_sessions(SessionFilters(), 50, 0)

    assert [item.agent_id for item in page.items] == ["hr-bot"]
    assert repository.list_sessions(
        SessionFilters(agent_id="fae-bot"), 50, 0
    ).total == 0
    assert repository.get_session(fae_session_key) is None
    assert repository.get_trace(fae_turn_key) is None
    assert repository.get_agent("fae-bot") is None
    assert repository.get_agent("codex-assistant") is None
    assert repository.get_agent("ai-fae-agent") is not None
    assert "fae-bot" not in {
        item.bot_id for item in repository.usage_snapshot().records
    }


def test_usage_leaders_count_answered_turns_not_sessions():
    now = datetime(2026, 8, 27, 1, 24, tzinfo=UTC)
    records = []
    expected_turns = 0
    for session_index in range(15):
        turn_count = 2 if session_index == 14 else 3
        record = _record(
            now,
            key=f"{session_index + 1:052x}",
            agent_id="ai-fae-agent",
            turn_key=f"{session_index + 101:052x}",
        )
        prototype = record["turns"][0]
        record["turns"] = []
        for turn_index in range(turn_count):
            turn = deepcopy(prototype)
            turn["key"] = f"{session_index * 10 + turn_index + 201:052x}"
            turn["turn_index"] = turn_index + 1
            turn["created_at"] = (
                now - timedelta(minutes=session_index + turn_index)
            ).isoformat().replace("+00:00", "Z")
            record["turns"].append(turn)
            expected_turns += 1
        records.append(record)

    empty_answer = _record(
        now, key="e" * 52, agent_id="ai-fae-agent", turn_key="8" * 52
    )
    empty_answer["turns"][0]["answer"]["text"] = ""
    records.append(empty_answer)

    outside_window = _record(
        now, key="d" * 52, agent_id="ai-fae-agent", turn_key="7" * 52
    )
    outside_window["turns"][0]["created_at"] = (
        now - timedelta(hours=25)
    ).isoformat().replace("+00:00", "Z")
    records.append(outside_window)

    records.append(_record(
        now, key="c" * 52, agent_id="test-bot", turn_key="6" * 52
    ))
    repository, _ = _repository(now, tuple(records))

    leaders = repository.usage_leaders(
        now - timedelta(hours=24), now, "business"
    )

    assert expected_turns == 44
    assert [(item.agent_id, item.agent_name, item.conversations) for item in leaders] == [
        ("ai-fae-agent", "AI FAE Agent", 44),
    ]
