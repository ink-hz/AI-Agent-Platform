from collections.abc import Callable

from app.config import Config
from app.local_secrets import SecretFileUnavailable, read_secret_file


SecretReader = Callable[[str], str]


def resolve_flywheel_database_url(
    config: Config,
    reader: SecretReader = read_secret_file,
) -> str | None:
    if not config.flywheel_enabled:
        return None
    if config.flywheel_database_url:
        return config.flywheel_database_url

    try:
        return reader(config.flywheel_database_url_file)
    except SecretFileUnavailable:
        return None
