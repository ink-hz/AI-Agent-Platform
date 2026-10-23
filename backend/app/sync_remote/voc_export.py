"""VOC bot observability over the established trusted SSH source transport."""

from __future__ import annotations

import inspect
import json
from collections import Counter

from .config import SyncSource

VOC_TABLES = ("chat_sessions", "chat_turns")

VOC_SQL = """
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT json_build_object(
  'database', current_database(),
  'schema_ready', EXISTS (SELECT 1 FROM voc_meta.schema_migrations
    WHERE name='020_employee_chatbot_experience.sql'),
  'rows', COALESCE(json_agg(row_to_json(source) ORDER BY source.received_at, source.id), '[]'::json)
) FROM (
  SELECT inbound.id, inbound.sender_internal_user_id AS internal_user_id,
    inbound.conversation_id, inbound.conversation_type,
    inbound.sender_name, inbound.sender_dept AS sender_department,
    inbound.received_at, inbound.dingtalk_created_at AS question_at,
    inbound.text_content AS question,
    CASE WHEN delivery.status='sent' THEN delivery.content ELSE '' END AS answer,
    CASE WHEN delivery.status='sent' THEN delivery.sent_at END AS answer_at,
    job.status AS job_status, delivery.status AS delivery_status
  FROM voc_raw.inbound_messages inbound
  LEFT JOIN voc_raw.interaction_jobs job ON job.inbound_message_id=inbound.id
  LEFT JOIN voc_core.outbox_messages delivery ON delivery.source_inbound_message_id=inbound.id
) source;
COMMIT;
""".strip()


def project_voc_rows(rows: list[dict]) -> dict[str, list[dict]]:
    """Project real source facts; never synthesize deliveries or employee IDs."""
    from datetime import datetime
    from uuid import NAMESPACE_URL, UUID, uuid5

    def timestamp(value):
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("source_timestamp_invalid")
        return parsed

    sessions = {}
    turn_counts = Counter()
    turns = []
    seen = set()
    for row in sorted(rows, key=lambda item: (timestamp(item["received_at"]), int(item["id"]))):
        inbound_id = int(row["id"])
        if inbound_id < 1 or inbound_id in seen:
            raise ValueError("source_row_invalid")
        seen.add(inbound_id)
        owner = str(UUID(str(row["internal_user_id"]))) if row.get("internal_user_id") else None
        group = json.dumps([
            owner or f"unresolved:{inbound_id}",
            row["conversation_type"], row.get("conversation_id"),
        ], ensure_ascii=False, separators=(",", ":"))
        sid = str(uuid5(NAMESPACE_URL, "orbbec-voc-session:" + group))
        received = timestamp(row["received_at"])
        question_at = timestamp(row.get("question_at"))
        delivered = row.get("delivery_status") == "sent"
        answer = (row.get("answer") or "") if delivered else ""
        answer_at = timestamp(row.get("answer_at")) if delivered else None
        if not isinstance(answer, str) or (delivered and (not answer or answer_at is None)):
            raise ValueError("source_delivery_invalid")
        question = row.get("question") or ""
        if not isinstance(question, str):
            raise ValueError("source_question_invalid")
        last_active = max(received, answer_at) if answer_at else received
        if sid not in sessions:
            sessions[sid] = {
                "id": sid, "external_session_id": "voc:" + sid,
                "channel": "dingtalk", "user_id": owner, "external_user_id": None,
                "owner_subject_id": owner,
                "owner_subject_type": "enterprise_member" if owner else None,
                "conversation_title": question[:180] or None,
                "primary_sender_name": row.get("sender_name"),
                "primary_sender_department": row.get("sender_department"),
                "created_at": received.isoformat(), "last_active_at": last_active.isoformat(),
                "metadata": {"identity_source": "voc_persisted_internal_uuid"},
            }
        session = sessions[sid]
        if last_active > timestamp(session["last_active_at"]):
            session["last_active_at"] = last_active.isoformat()
        if delivered:
            outcome = "sent"
        elif row.get("delivery_status"):
            outcome = "delivery_failed" if row["delivery_status"] == "failed" else "delivery_pending"
        else:
            outcome = {"pending": "pending", "claimed": "processing", "failed": "failed"}.get(row.get("job_status"), "unavailable")
        elapsed = (answer_at - received).total_seconds() * 1000 if answer_at else None
        turn_counts[sid] += 1
        turns.append({
            "id": str(uuid5(NAMESPACE_URL, f"orbbec-voc-inbound:{inbound_id}")),
            "session_id": sid, "external_session_id": session["external_session_id"],
            "turn_index": turn_counts[sid],
            "trace_id": None, "channel": "dingtalk", "question": question,
            "answer": answer, "sources": [], "stages": [], "done": {"sent": delivered},
            "planned_capabilities": [], "capability_coverage": {},
            "fallback_used": None, "fallback_reason": None, "outcome": outcome,
            "duration_ms": int(elapsed) if elapsed is not None and elapsed >= 0 else None,
            "created_at": (answer_at or received).isoformat(),
            "question_at": question_at.isoformat() if question_at else None,
            "answer_at": answer_at.isoformat() if answer_at else None,
            "metadata": {"agent_id": "voc", "source_inbound_id": inbound_id},
        })
    return {"chat_sessions": list(sessions.values()), "chat_turns": turns}


def build_voc_program(source: SyncSource) -> str:
    from .export import ExportError

    if tuple(t.remote_name for t in source.tables) != VOC_TABLES or any(
        t.target_name != t.remote_name or t.order_by != "id" for t in source.tables
    ):
        raise ExportError("source_table_not_allowed")
    return f"""
import json
import subprocess
from collections import Counter
{inspect.getsource(project_voc_rows)}
result = subprocess.run(
    ['docker', 'exec', '-i', 'orbbec-voc-postgres-1', 'psql', '-X', '-qAt',
     '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'orbbec_voc'],
    input={VOC_SQL!r}, text=True, capture_output=True, timeout=60, check=False,
)
if result.returncode != 0:
    raise RuntimeError('voc_source_unavailable')
snapshot = json.loads(result.stdout)
if snapshot.get('database') != 'orbbec_voc' or snapshot.get('schema_ready') is not True:
    raise RuntimeError('voc_source_identity_unverified')
tables = project_voc_rows(snapshot['rows'])
print(json.dumps({{'record_type': 'source_identity', 'agent_id': 'voc', 'schema': 'voc_raw/voc_core'}}))
for table, rows in tables.items():
    for row in rows:
        print(json.dumps({{'record_type': 'table_row', 'table': table, 'row': row}}, ensure_ascii=False))
    print(json.dumps({{'record_type': 'table_complete', 'table': table, 'count': len(rows)}}))
""".strip()


def validate_voc_records(output: str) -> None:
    from .export import ExportError

    identity = False
    completed = {}
    counts = Counter()
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            raise ExportError("source_snapshot_incomplete") from None
        if not isinstance(record, dict):
            raise ExportError("source_snapshot_incomplete")
        kind = record.get("record_type")
        if kind == "source_identity":
            if identity or record.get("agent_id") != "voc" or record.get("schema") != "voc_raw/voc_core":
                raise ExportError("source_identity_unverified")
            identity = True
        elif kind in {"table_row", "table_complete"}:
            table = record.get("table")
            if not identity or table not in VOC_TABLES:
                raise ExportError("source_table_not_allowed")
            if kind == "table_row":
                if not isinstance(record.get("row"), dict) or table in completed:
                    raise ExportError("source_snapshot_incomplete")
                counts[table] += 1
            elif table in completed or type(record.get("count")) is not int:
                raise ExportError("source_snapshot_incomplete")
            else:
                completed[table] = record["count"]
        else:
            raise ExportError("source_snapshot_incomplete")
    if not identity:
        raise ExportError("source_identity_unverified")
    if set(completed) != set(VOC_TABLES) or any(counts[t] != n for t, n in completed.items()):
        raise ExportError("source_snapshot_incomplete")
