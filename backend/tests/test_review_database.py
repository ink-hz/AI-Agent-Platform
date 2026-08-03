from dataclasses import replace
from subprocess import CompletedProcess

from app.config import load_config
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
        runner=lambda *args, **kwargs: CompletedProcess(
            args=[], returncode=1, stdout="", stderr="analyst secret"
        ),
    )

    assert result is None


def test_review_database_url_uses_explicit_writer_dsn(monkeypatch):
    monkeypatch.setenv(
        "PLATFORM_REVIEW_DATABASE_URL", "postgresql://review-writer"
    )
    config = load_config()

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("explicit writer DSN must bypass Keychain")

    assert resolve_review_database_url(config, runner=must_not_run) == (
        "postgresql://review-writer"
    )
