import json
from pathlib import Path

from pydantic import ValidationError

from .models import MonitorTarget


class ContractLoadError(RuntimeError):
    """A safe, user-facing runtime contract error."""


def _target_from_entry(entry: object) -> MonitorTarget:
    if not isinstance(entry, dict):
        raise ContractLoadError("invalid bot entry")
    instance = entry.get("instance")
    if not isinstance(instance, dict):
        raise ContractLoadError("invalid bot entry")
    name = entry.get("name")
    pm2_name = instance.get("pm2Name")
    port = instance.get("apiPort")
    workdir = entry.get("workdir", "")
    if not isinstance(name, str) or not name.strip():
        raise ContractLoadError("invalid bot entry")
    if not isinstance(pm2_name, str) or not pm2_name.strip():
        raise ContractLoadError("invalid bot entry")
    if not isinstance(workdir, str):
        raise ContractLoadError("invalid bot entry")
    try:
        return MonitorTarget(
            id=name,
            name=name,
            pm2_name=pm2_name,
            port=port,
            health_url=f"http://127.0.0.1:{port}/api/health",
            workdir=workdir,
        )
    except ValidationError as error:
        raise ContractLoadError("invalid bot entry") from error


def _reject_duplicates(targets: list[MonitorTarget]) -> None:
    ids: set[str] = set()
    ports: set[int] = set()
    for target in targets:
        if target.id in ids:
            raise ContractLoadError("duplicate bot id")
        if target.port in ports:
            raise ContractLoadError("duplicate api port")
        ids.add(target.id)
        ports.add(target.port)


def load_targets(path: str) -> list[MonitorTarget]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ContractLoadError("runtime contract not found") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractLoadError("runtime contract is unreadable") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("bots", []), list):
        raise ContractLoadError("invalid runtime contract")

    entries = list(payload.get("bots", []))
    test_bot = payload.get("testBot")
    if isinstance(test_bot, dict) and test_bot.get("enabled", True):
        entries.append(test_bot)

    targets = [_target_from_entry(entry) for entry in entries]
    _reject_duplicates(targets)
    return targets
