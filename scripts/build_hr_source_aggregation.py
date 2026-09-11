#!/usr/bin/env python3
"""Publish existing rule aggregates against the exact source reading edition."""
import argparse
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def build(bundle: Path, sources: Path):
    manifest = json.loads((sources / "manifest.json").read_text())
    archive = json.loads((bundle / "manifest.json").read_text())
    assert archive["bundle_id"] == manifest["source_bundle_id"]
    raw = (bundle / "aggregates.json").read_bytes()
    checksums = (bundle / "checksums.sha256").read_text().splitlines()
    assert any(line.split() == [hashlib.sha256(raw).hexdigest(), "aggregates.json"] for line in checksums)
    aggregates = json.loads(raw)
    normalized = [json.loads(line) for line in (bundle / "normalized-jobs.jsonl").read_text().splitlines() if line]
    published = []
    companies = {}
    for entry in manifest["companies"]:
        body = (sources / entry["path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == entry["sha256"]
        jobs = json.loads(body)["jobs"]
        published.extend((entry["company_key"], job["job_id"], job["evidence_sha256"]) for job in jobs)
        metrics = aggregates["company_matrix"].get(entry["company_key"])
        if metrics is not None:
            assert metrics["job_count"] == len(jobs)
        companies[entry["company_key"]] = metrics
    assert sorted(published) == sorted((j["company_key"], j["job_id"], j["evidence_sha256"]) for j in normalized)
    output = {"schema_version": 1, "source_edition": manifest["edition"],
              "source_bundle_id": manifest["source_bundle_id"], "rules_schema_version": aggregates["schema_version"],
              "archive_aggregates_sha256": hashlib.sha256(raw).hexdigest(),
              "companies": companies}
    output["edition"] = "aggregation-" + hashlib.sha256(canonical(output)).hexdigest()[:20]
    (sources / "aggregation.json").write_bytes(canonical(output) + b"\n")
    print(output["edition"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    args = parser.parse_args()
    build(args.bundle, args.sources)
