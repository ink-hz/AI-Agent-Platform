"""Explicit persisted observations for authority tests, through the real validator."""

import hashlib
import json
from datetime import datetime, timezone


def observation():
    config = {
        "targetBot": "hr-bot",
        "engine": "claude",
        "backend": "pty",
        "model": "test-model",
        "compatibilityProfile": None,
        "toolPolicy": "default",
    }
    config["configHash"] = hashlib.sha256(
        json.dumps(
            config, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    return {
        "version": "hr_v5_readiness_v1",
        "sampledAt": datetime.now(timezone.utc).isoformat(),
        "callbackOrigin": "http://127.0.0.1:19191",
        "receiverReady": True,
        "uploadReady": True,
        "handoffReady": True,
        "reason": "ready",
        "service": {
            "contractVersion": "core_chat_collaboration_v5",
            "callbackOrigin": "http://127.0.0.1:19191",
            "durableTerminal": True,
            "healthy": True,
            "capacity": "available",
            "reason": "ready",
            "config": config,
        },
    }
