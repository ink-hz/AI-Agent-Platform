from __future__ import annotations

import json
import sys

from .directory_worker import DirectoryWorkerRepository
from .worker_runtime import load_worker_settings

_FAILED_MARKER = "DINGTALK_EMPLOYEE_PROFILE_PROBE_FAILED"


def main() -> int:
    try:
        settings = load_worker_settings("directory")
        if settings.directory_database_url is None:
            raise RuntimeError("directory worker configuration unavailable")
        readiness = DirectoryWorkerRepository(
            settings.directory_database_url
        ).read_employee_profile_readiness()
    except KeyboardInterrupt:
        raise
    except Exception:
        sys.stderr.write(f"{_FAILED_MARKER}\n")
        return 1

    sys.stdout.write(
        json.dumps(readiness.as_public_dict(), sort_keys=True) + "\n"
    )
    if readiness.ready:
        return 0
    sys.stderr.write(f"{_FAILED_MARKER}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
