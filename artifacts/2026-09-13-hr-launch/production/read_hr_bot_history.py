"""Read only the HR Bot SQLite snapshot; route stdout to a private file."""
import datetime
import json
import sqlite3
import sys


path = "/Users/agentops/AgentRuntime/instances/hr-bot/state/sessions.db"
with sqlite3.connect("file:" + path + "?mode=ro", uri=True) as connection:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    sessions = [dict(r) for r in connection.execute(
        "SELECT id,claude_session_id,platform,created_at,updated_at FROM sessions "
        "WHERE bot_name='hr-bot' ORDER BY created_at,id"
    )]
    messages = [dict(r) for r in connection.execute(
        "SELECT m.id,m.session_id,m.role,m.text,m.platform,m.timestamp "
        "FROM session_messages m JOIN sessions s ON s.id=m.session_id "
        "WHERE s.bot_name='hr-bot' AND m.role='user' ORDER BY m.session_id,m.timestamp,m.id"
    )]
    json.dump({
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "HR Bot sessions.db snapshot, user messages only, all stored channels",
        "sessions": sessions, "messages": messages,
    }, sys.stdout, ensure_ascii=False)
