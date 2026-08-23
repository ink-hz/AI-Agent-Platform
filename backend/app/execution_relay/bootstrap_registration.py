from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sys
from uuid import UUID

import psycopg

from .register_worker import _public_document, _secret_file


REFERENCE = "AGENT_BRAIN_BOOTSTRAP_001"
REQUEST_ID = UUID("8e03a2df-8413-4b38-9d51-6f970e1fd2a4")


class BootstrapRegistrationError(RuntimeError):
    """First-Worker state is absent or exact; every other state is rejected."""


@dataclass(frozen=True)
class BootstrapWorkerDocument:
    worker_id: str
    key_id: str
    public_key: bytes
    allowed_agent_ids: tuple[str, ...]

    @classmethod
    def from_file(cls, path: str) -> "BootstrapWorkerDocument":
        worker, key, public, agents = _public_document(path)
        return cls(worker, key, public, agents)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.public_key).hexdigest()


def ensure_first_worker(connection, document: BootstrapWorkerDocument) -> str:
    row = connection.execute(
        "select platform_control.ensure_first_execution_worker_v33("
        "%s,%s,%s,%s,%s,%s)",
        (
            document.worker_id,
            document.key_id,
            document.public_key,
            list(document.allowed_agent_ids),
            REFERENCE,
            REQUEST_ID,
        ),
    ).fetchone()
    if row is None or row[0] not in {"registered", "existing"}:
        raise BootstrapRegistrationError("Worker registration verification failed")
    return row[0]


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    try:
        if len(values) != 1:
            raise ValueError
        document = BootstrapWorkerDocument.from_file(values[0])
        with psycopg.connect(_secret_file()) as connection:
            status = ensure_first_worker(connection, document)
        print(
            f"EXECUTION_WORKER_BOOTSTRAP_OK status={status} "
            f"fingerprint={document.fingerprint}"
        )
        return 0
    except Exception:
        print("EXECUTION_WORKER_BOOTSTRAP_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
