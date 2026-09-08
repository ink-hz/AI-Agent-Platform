"""Regenerate frontend API fixtures from one checksum-verified real bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.hr.company_intelligence import project_companies, project_company
from app.hr.intelligence_bundle import verify_import_bundle


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    bundle = verify_import_bundle(args.bundle)
    record = {
        "bundle_id": bundle.bundle_id,
        "generated_at": bundle.generated_at,
        "source_catalog": bundle.catalog,
        "source_coverage": bundle.coverage,
        "aggregates": bundle.aggregates,
        "analysis": bundle.analysis,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    companies = project_companies(record)
    detail = project_company(record, "insta360")
    missing_metrics = project_company(record, "scantech")
    selected_jobs = [dict(job) for job in bundle.jobs if job.get("company_key") == "insta360"]
    jobs = {
        "bundle_id": str(bundle.bundle_id), "company_key": "insta360",
        "items": selected_jobs[:25], "total": len(selected_jobs), "offset": 0, "limit": 25,
    }
    _write(args.output / "companies.json", companies)
    _write(args.output / "company-insta360.json", detail)
    _write(args.output / "company-scantech-missing-metrics.json", missing_metrics)
    _write(args.output / "company-insta360-jobs-page.json", jobs)
    _write(args.output / "provenance.json", {
        "source_bundle_id": str(bundle.bundle_id),
        "source_manifest_sha256": bundle.manifest_sha256,
        "source_generated_at": bundle.generated_at.isoformat(),
        "extraction_script": "extract_fixture.py",
    })


if __name__ == "__main__":
    main()
