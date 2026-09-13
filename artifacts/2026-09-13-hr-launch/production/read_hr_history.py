"""Authorized read-only HR history export; stdout MUST go to a private file.

Run inside the existing API container. Uses deployed keys in place, never prints
secrets, does not export assistant answers or attachment bytes, and cannot write DB.
"""
import datetime
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec, SealedContent


codec = ContentCodec(IdentityKeyring.from_file(
    os.environ["PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE"],
    expected_purpose="platform-content-encryption", expected_key_length=32,
))
with psycopg.connect(
    Path(os.environ["PLATFORM_CONTROL_DATABASE_URL_FILE"]).read_text().strip(),
    row_factory=dict_row,
) as connection:
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    connection.execute("SET LOCAL statement_timeout='3s'")
    conversations = connection.execute(
        "SELECT conversation_id,owner_internal_user_id,status,created_at,updated_at "
        "FROM platform_control.conversations WHERE direct_agent_id='hr-bot' "
        "ORDER BY created_at,conversation_id"
    ).fetchall()
    rows = connection.execute(
        "SELECT c.owner_internal_user_id,m.conversation_id,m.message_id,m.seq,m.turn_id,"
        "m.created_at,m.content_ciphertext,m.encryption_key_version,t.status AS turn_status "
        "FROM platform_control.conversations c "
        "JOIN platform_control.conversation_messages m USING(conversation_id) "
        "LEFT JOIN platform_control.conversation_turns t ON t.turn_id=m.turn_id "
        "WHERE c.direct_agent_id='hr-bot' AND m.role='user' "
        "ORDER BY c.created_at,m.conversation_id,m.seq"
    ).fetchall()
    messages = []
    for row in rows:
        value = codec.unseal_json(
            f"conversation:{row['conversation_id']}:message:{row['message_id']}:content",
            SealedContent(bytes(row.pop("content_ciphertext")), row.pop("encryption_key_version")),
        )
        row["text"] = value["text"]
        row["has_resource_selections"] = bool(value.get("user_selected_resources"))
        messages.append(row)
    json.dump({
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "all HR direct-agent sessions, active and archived, user messages only",
        "boundary": "authorized operational read-only extraction, not cross-owner public API access",
        "conversations": conversations, "messages": messages,
    }, __import__("sys").stdout, ensure_ascii=False, default=str)
