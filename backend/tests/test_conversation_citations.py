from __future__ import annotations

# Imported fixture names intentionally become pytest fixtures in this module.
# ruff: noqa: F401,F811
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest
from app.attachments.citation_service import (
    CitationInput,
    CitationRepository,
    CitationService,
)
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from test_control_plane_migration import control_database
from test_conversation_attachment_migration import _seed_task

CONVERSATION_ID = UUID("11111111-1111-4111-8111-111111111111")
MESSAGE_ID = UUID("22222222-2222-4222-8222-222222222222")
RETRIEVED_AT = datetime(2026, 9, 3, 10, tzinfo=UTC)


class Repository:
    def __init__(self) -> None:
        self.calls = []

    def record(self, conversation_id, message_id, citations):
        self.calls.append((conversation_id, message_id, citations))
        return citations


def citation() -> CitationInput:
    return CitationInput(
        citation_key="source-1",
        title="Anthropic documentation",
        url="HTTPS://Example.COM:443/docs?q=1#section",
        site="example.com",
        retrieved_at=RETRIEVED_AT,
        supports=("answer:0-42",),
    )


def test_citations_are_canonicalized_and_persist_retrieval_provenance() -> None:
    repository = Repository()
    service = CitationService(repository)

    result = service.record(CONVERSATION_ID, MESSAGE_ID, (citation(),))

    assert result[0].url == "https://example.com/docs?q=1#section"
    assert result[0].site == "example.com"
    assert result[0].retrieved_at == RETRIEVED_AT
    assert result[0].supports == ("answer:0-42",)
    assert repository.calls == [(CONVERSATION_ID, MESSAGE_ID, result)]


@pytest.mark.parametrize(
    "bad",
    [
        {"url": "file:///etc/passwd"},
        {"url": "https://user:secret@example.com/"},
        {"url": "https://example.com/\nsecret"},
        {"url": "https://example.com/" + "x" * 4096},
        {"site": "other.example"},
        {"title": "unsafe\x00title"},
        {"supports": ()},
        {"supports": ("x" * 129,)},
    ],
)
def test_citations_reject_active_or_ambiguous_urls_and_oversized_fields(bad) -> None:
    value = replace(citation(), **bad)
    service = CitationService(Repository())

    with pytest.raises(ValueError):
        service.record(CONVERSATION_ID, MESSAGE_ID, (value,))


def test_duplicate_citation_keys_are_rejected_before_database_write() -> None:
    repository = Repository()
    service = CitationService(repository)

    with pytest.raises(ValueError):
        service.record(CONVERSATION_ID, MESSAGE_ID, (citation(), citation()))

    assert repository.calls == []


@pytest.mark.postgres
def test_citation_repository_encrypts_values_and_replays_exact_message_set(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
    codec = ContentCodec(
        IdentityKeyring(
            active_version=2,
            purpose="platform-content-encryption",
            _keys={1: b"1" * 32, 2: b"2" * 32},
        )
    )
    service = CitationService(
        CitationRepository(
            environment["urls"]["platform_control_app"], content_codec=codec
        )
    )
    expected = replace(citation(), url="https://example.com/docs?q=1#section")

    assert service.record(
        context["conversation_id"], context["message_id"], (citation(),)
    ) == (expected,)
    assert service.record(
        context["conversation_id"], context["message_id"], (citation(),)
    ) == (expected,)

    with psycopg.connect(environment["admin"]) as admin:
        row = admin.execute(
            "select citation_key,url_ciphertext,site_ciphertext,title_ciphertext,"
            "supported_claim_locations from platform_attachments.message_citations "
            "where message_id=%s",
            (context["message_id"],),
        ).fetchone()
    assert row[0] == "source-1"
    assert b"example.com" not in bytes(row[1])
    assert b"example.com" not in bytes(row[2])
    assert b"Anthropic" not in bytes(row[3])
    assert row[4] == ["answer:0-42"]
