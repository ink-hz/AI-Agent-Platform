"""Recompute retained synthetic-run evidence; no database or network access."""

import hashlib
import json
from pathlib import Path


def summarize(root):
    rows = []
    for directory in sorted(root.glob("run-*")):
        data = json.loads((directory / "evidence.json").read_text())
        row = {"run": directory.name, "generation_calls": data["generation_calls"], "stages": []}
        attempt_count = 0
        for stage in data["stages"]:
            number = len(row["stages"]) + 1
            for result in stage["results"]:
                assert (directory / f"stage-{number}-{result['kind']}.md").read_text() == result["body"]
            attempts = stage["attempts"]
            attempt_count += len(attempts)
            usage = [a["reply"]["usage"] for a in attempts if a.get("reply")]
            row["stages"].append({
                "name": stage["name"], "state": stage["work"]["state"],
                "saved_kinds": [r["kind"] for r in stage["results"]],
                "attempts": len(attempts),
                "interrupted": sum(a["status"] == "interrupted" for a in attempts),
                "captured_reply_reported_tokens": sum((u["input_total"] or 0) + (u["output_total"] or 0) for u in usage),
                "charged_tokens": stage["work"]["budget"]["charged_tokens"],
                "charged_usage_quality": stage["work"]["budget"]["usage_quality"],
            })
        row["retained_attempts"] = attempt_count
        rows.append(row)
    return rows


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    manifest = root / "sha256-manifest.json"
    if manifest.exists():
        for entry in json.loads(manifest.read_text()):
            raw = (root / entry["path"]).read_bytes()
            assert len(raw) == entry["bytes"]
            assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
    print(json.dumps(summarize(root), ensure_ascii=False, indent=2))
