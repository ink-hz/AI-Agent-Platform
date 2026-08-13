from __future__ import annotations

from pathlib import Path

from app.local_secrets import read_secret_file


def read_control_migrator_database_url(secret_file: str | Path) -> str:
    """Load the deployment-only control database DSN from a private file."""
    return read_secret_file(str(secret_file))
