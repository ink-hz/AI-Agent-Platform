from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SourceKind = Literal["fae", "admin"]


@dataclass(frozen=True)
class TableExport:
    remote_name: str
    target_name: str


@dataclass(frozen=True)
class SyncSource:
    kind: SourceKind
    ssh_host: str
    ssh_key_path: str
    remote_python: str
    remote_container: str | None
    tables: tuple[TableExport, ...]
    trace_jsonl_path: str | None = None


def default_sources(ssh_host: str, ssh_key_path: str) -> dict[SourceKind, SyncSource]:
    return {
        "fae": SyncSource(
            kind="fae",
            ssh_host=ssh_host,
            ssh_key_path=ssh_key_path,
            remote_python="python",
            remote_container="ai-fae-backend",
            tables=(
                TableExport("chat_sessions", "chat_sessions"),
                TableExport("chat_turns", "chat_turns"),
                TableExport("turn_feedback", "turn_feedback"),
                TableExport("turn_reviews", "turn_reviews"),
                TableExport("eval_candidates", "eval_candidates"),
                TableExport(
                    "knowledge_improvement_tasks",
                    "knowledge_improvement_tasks",
                ),
                TableExport("qa_review_items", "qa_review_items"),
            ),
            trace_jsonl_path="/app/data/logs/traces.jsonl",
        ),
        "admin": SyncSource(
            kind="admin",
            ssh_host=ssh_host,
            ssh_key_path=ssh_key_path,
            remote_python="/opt/ai-admin-agent/.venv/bin/python",
            remote_container=None,
            tables=(
                TableExport("admin_chat_sessions", "admin_chat_sessions"),
                TableExport("admin_chat_turns", "admin_chat_turns"),
                TableExport("admin_turn_feedback", "admin_turn_feedback"),
                TableExport("admin_turn_reviews", "admin_turn_reviews"),
                TableExport("admin_eval_candidates", "admin_eval_candidates"),
                TableExport(
                    "admin_knowledge_improvement_tasks",
                    "admin_knowledge_improvement_tasks",
                ),
            ),
        ),
    }

