from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
import app.attachments  # noqa: E402

source = Path(__file__).with_name("object_writer-a608970b.py")
spec = importlib.util.spec_from_file_location("app.attachments.object_writer", source)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
setattr(app.attachments, "object_writer", module)
spec.loader.exec_module(module)
raise SystemExit(
    pytest.main(
        [
            "-q",
            str(BACKEND / "tests/test_attachment_object_writer_botocore.py"),
            str(BACKEND / "tests/test_attachment_upload_service.py::test_object_writer_rejects_extra_bytes_and_fences_the_confirmed_write"),
            str(BACKEND / "tests/test_attachment_upload_service.py::test_suspended_null_fence_is_untouched_when_size_validation_fails"),
            str(BACKEND / "tests/test_attachment_s3_version_erasure.py::test_size_mismatch_is_rejected_before_any_versioned_put_or_delete"),
        ]
    )
)
