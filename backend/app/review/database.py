from collections.abc import Callable

from app.config import Config
from app.local_secrets import SecretFileUnavailable, read_secret_file


SecretReader = Callable[[str], str]


def resolve_review_database_url(
    config: Config,
    reader: SecretReader = read_secret_file,
) -> str | None:
    """Resolve only the dedicated review-writer credential."""
    if not config.review_enabled:
        return None
    if config.review_database_url:
        return config.review_database_url

    try:
        return reader(config.review_database_url_file)
    except SecretFileUnavailable:
        return None
