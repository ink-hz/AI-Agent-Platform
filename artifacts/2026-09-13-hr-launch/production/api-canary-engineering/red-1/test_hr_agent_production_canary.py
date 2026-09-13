"""API canary engineering: real local identity/PG; no production or real model."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

SCRIPT = Path(__file__).parents[2] / "artifacts/2026-09-13-hr-launch/production/api_canary.py"


def canary():
    assert SCRIPT.exists(), "bounded API canary is missing"
    spec = importlib.util.spec_from_file_location("api_canary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def credentials(tmp_path, **changes):
    value = dict(owner_id=str(uuid4()), session_cookie="private-session", csrf="private-csrf",
                 public_origin="https://localhost", api_base_url="https://localhost")
    value.update(changes)
    path = tmp_path / "private.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def test_private_config_requires_absolute_regular_0600_and_same_https_origin(tmp_path):
    h = canary()
    path = credentials(tmp_path)
    assert h.load_config(path)["api_base_url"] == "https://localhost"
    path.chmod(0o644)
    with pytest.raises(h.CanaryError):
        h.load_config(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(h.CanaryError):
        h.load_config(link)
    for change in ({"api_base_url": "https://other.invalid"}, {"csrf": "x\r\nX: leak"},
                   {"public_origin": "http://localhost"}, {"session_cookie": ""}):
        with pytest.raises(h.CanaryError):
            h.load_config(credentials(tmp_path, **change))
    with pytest.raises(h.CanaryError):
        h.load_config(Path("relative.json"))


def test_unknown_upload_acceptance_is_journaled_and_never_retried(tmp_path):
    import httpx
    h = canary()
    calls = []
    def lose(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-session must never escape")
    client = httpx.Client(transport=httpx.MockTransport(lose))
    runner = h.Canary(h.load_config(credentials(tmp_path)), tmp_path / "run", client)
    with pytest.raises(h.CanaryError, match="outcome_unknown"):
        runner.mutate("upload_begin", "/api/v1/attachments/uploads", {"original_name": "synthetic.txt"}, (201,))
    key = runner.ledger["operations"]["upload_begin"]["key"]
    assert str(__import__("uuid").UUID(key)) == key
    with pytest.raises(h.CanaryError, match="outcome_unknown"):
        runner.mutate("upload_begin", "/api/v1/attachments/uploads", {"original_name": "synthetic.txt"}, (201,))
    assert len(calls) == 1
    evidence = (tmp_path / "run" / "ledger.json").read_text()
    assert "private-session" not in evidence and "private-csrf" not in evidence
    assert json.loads(evidence)["operations"]["upload_begin"]["status"] == "outcome_unknown"
