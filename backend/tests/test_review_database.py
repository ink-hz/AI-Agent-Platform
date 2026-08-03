from dataclasses import replace

from app.config import load_config
from app.local_secrets import SecretFileUnavailable
from app.review.database import resolve_review_database_url


def test_review_database_url_never_falls_back_to_analyst_dsn(monkeypatch):
    monkeypatch.delenv("PLATFORM_REVIEW_DATABASE_URL", raising=False)
    config = replace(
        load_config(),
        review_enabled=True,
        review_database_url=None,
        flywheel_database_url="postgresql://analyst",
    )

    result = resolve_review_database_url(
        config,
        reader=lambda *_args: (_ for _ in ()).throw(
            SecretFileUnavailable("secret file unavailable")
        ),
    )

    assert result is None


def test_review_database_url_uses_explicit_writer_dsn(monkeypatch):
    monkeypatch.setenv(
        "PLATFORM_REVIEW_DATABASE_URL", "postgresql://review-writer"
    )
    config = load_config()

    def must_not_read(*_args, **_kwargs):
        raise AssertionError("explicit writer DSN must bypass the secret file")

    assert resolve_review_database_url(config, reader=must_not_read) == (
        "postgresql://review-writer"
    )


def test_review_database_uses_only_writer_secret_file(monkeypatch):
    monkeypatch.delenv("PLATFORM_REVIEW_DATABASE_URL", raising=False)
    config = replace(
        load_config(),
        review_database_url=None,
        flywheel_database_url="postgresql://analyst",
    )
    seen = []

    def read(path):
        seen.append(path)
        return "postgresql://review-writer"

    assert resolve_review_database_url(config, reader=read) == (
        "postgresql://review-writer"
    )
    assert seen == [config.review_database_url_file]


def test_review_database_secret_file_failure_is_fail_closed(monkeypatch):
    monkeypatch.delenv("PLATFORM_REVIEW_DATABASE_URL", raising=False)
    config = replace(load_config(), review_database_url=None)

    def failed(*_args, **_kwargs):
        raise SecretFileUnavailable("secret file unavailable")

    assert resolve_review_database_url(config, reader=failed) is None
