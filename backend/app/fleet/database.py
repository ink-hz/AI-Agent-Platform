import subprocess
from collections.abc import Callable

from app.config import Config


Runner = Callable[..., subprocess.CompletedProcess[str]]


def resolve_flywheel_database_url(
    config: Config,
    runner: Runner = subprocess.run,
) -> str | None:
    if not config.flywheel_enabled:
        return None
    if config.flywheel_database_url:
        return config.flywheel_database_url

    result = runner(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            config.flywheel_keychain_account,
            "-s",
            config.flywheel_keychain_service,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or None
