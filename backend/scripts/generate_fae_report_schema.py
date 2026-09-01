from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.fae_reports.models import FaeAnalysisReport  # noqa: E402


OUTPUT = ROOT / "contracts" / "fae-analysis-report" / "v1" / "schema.json"


def main() -> None:
    schema = FaeAnalysisReport.model_json_schema(
        ref_template="#/$defs/{model}", mode="validation"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://agent.orbbec.com.cn/contracts/fae-analysis-report/v1/schema.json"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
