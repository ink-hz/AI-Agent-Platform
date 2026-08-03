import subprocess
from collections.abc import Callable

from app.config import Config


Runner = Callable[..., subprocess.CompletedProcess[str]]


def resolve_review_database_url(
    config: Config,
    runner: Runner = subprocess.run,
) -> str | None:
    """Resolve only the dedicated review-writer credential."""
    if not config.review_enabled:
        return None
    if config.review_database_url:
        return config.review_database_url

    result = runner(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            config.review_keychain_account,
            "-s",
            config.review_keychain_service,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or None
