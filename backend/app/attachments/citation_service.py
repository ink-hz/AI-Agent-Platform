from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, SealedContent

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_ENCODED_CRLF = re.compile(r"%0[ad]", re.IGNORECASE)


@dataclass(frozen=True)
class CitationInput:
    citation_key: str
    title: str
    url: str
    site: str
    retrieved_at: datetime
    supports: tuple[str, ...]


def _citation_subject(citation_id: UUID, field: str) -> str:
    return f"citation:{citation_id}:{field}"


class CitationRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        connect: Callable[..., object] = psycopg.connect,
    ) -> None:
        validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(content_codec, ContentCodec):
            raise TypeError("content codec required")
        self._database_url = control_database_url
        self._codec = content_codec
        self._connect = connect

    def __repr__(self) -> str:
        return "CitationRepository(database_url=<redacted>, content_codec=<redacted>)"

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def record(
        self,
        conversation_id: UUID,
        message_id: UUID,
        citations: tuple[CitationInput, ...],
    ) -> tuple[CitationInput, ...]:
        try:
            with self._connection() as connection, connection.transaction():
                for ordinal, citation in enumerate(citations, 1):
                    citation_id = uuid5(
                        NAMESPACE_URL,
                        f"conversation-citation-v64:{message_id}:{citation.citation_key}",
                    )
                    url = self._codec.seal_json(
                        _citation_subject(citation_id, "url"), {"url": citation.url}
                    )
                    site = self._codec.seal_json(
                        _citation_subject(citation_id, "site"), {"site": citation.site}
                    )
                    title = self._codec.seal_json(
                        _citation_subject(citation_id, "title"), {"title": citation.title}
                    )
                    connection.execute(
                        "insert into platform_attachments.message_citations("
                        "citation_id,conversation_id,message_id,ordinal,citation_key,"
                        "url_ciphertext,url_key_version,site_ciphertext,site_key_version,"
                        "title_ciphertext,title_key_version,supported_claim_locations,"
                        "retrieved_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "on conflict (message_id,citation_key) do nothing",
                        (
                            citation_id,
                            conversation_id,
                            message_id,
                            ordinal,
                            citation.citation_key,
                            url.ciphertext,
                            url.key_version,
                            site.ciphertext,
                            site.key_version,
                            title.ciphertext,
                            title.key_version,
                            Jsonb(list(citation.supports)),
                            citation.retrieved_at,
                        ),
                    )
                rows = connection.execute(
                    "select * from platform_attachments.message_citations "
                    "where conversation_id=%s and message_id=%s order by ordinal",
                    (conversation_id, message_id),
                ).fetchall()
            restored = tuple(self._from_row(row) for row in rows)
            if restored != citations:
                raise RuntimeError("citation replay conflict")
            return restored
        except RuntimeError:
            raise
        except Exception:  # noqa: BLE001 - storage/crypto failures are opaque
            raise RuntimeError("citation persistence unavailable") from None

    def _from_row(self, row: dict) -> CitationInput:
        def value(field: str) -> str:
            document = self._codec.unseal_json(
                _citation_subject(row["citation_id"], field),
                SealedContent(
                    bytes(row[f"{field}_ciphertext"]), int(row[f"{field}_key_version"])
                ),
            )
            if set(document) != {field} or not isinstance(document[field], str):
                raise RuntimeError("citation persistence unavailable")
            return document[field]

        return CitationInput(
            citation_key=row["citation_key"],
            title=value("title"),
            url=value("url"),
            site=value("site"),
            retrieved_at=row["retrieved_at"],
            supports=tuple(row["supported_claim_locations"]),
        )


class CitationService:
    def __init__(self, repository) -> None:
        self._repository = repository

    @staticmethod
    def _text(value: object, label: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise ValueError(f"citation {label} invalid")
        selected = value.strip()
        if len(selected.encode("utf-8")) > limit or any(
            character in selected for character in "\r\n"
        ):
            raise ValueError(f"citation {label} invalid")
        return selected

    @classmethod
    def _normalize(cls, citation: CitationInput) -> CitationInput:
        if not isinstance(citation, CitationInput):
            raise TypeError("citation input required")
        key = cls._text(citation.citation_key, "key", 64)
        if _KEY.fullmatch(key) is None:
            raise ValueError("citation key invalid")
        title = cls._text(citation.title, "title", 512)
        raw_url = cls._text(citation.url, "URL", 4096)
        if _CONTROL_OR_SPACE.search(raw_url) or _ENCODED_CRLF.search(raw_url):
            raise ValueError("citation URL invalid")
        try:
            parsed = urlsplit(raw_url)
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.hostname is None
            ):
                raise ValueError
            hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
            if not hostname or len(hostname.encode("ascii")) > 253:
                raise ValueError
            port = parsed.port
            host = f"[{hostname}]" if ":" in hostname else hostname
            default_port = (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}
            netloc = host if port is None or default_port else f"{host}:{port}"
            canonical_url = urlunsplit(
                (
                    parsed.scheme.lower(),
                    netloc,
                    parsed.path or "/",
                    parsed.query,
                    parsed.fragment,
                )
            )
        except (UnicodeError, ValueError):
            raise ValueError("citation URL invalid") from None
        site = cls._text(citation.site, "site", 253).encode("idna").decode("ascii").lower().rstrip(".")
        if site != hostname:
            raise ValueError("citation site invalid")
        if (
            not isinstance(citation.retrieved_at, datetime)
            or citation.retrieved_at.tzinfo is None
            or citation.retrieved_at.utcoffset() is None
        ):
            raise ValueError("citation retrieval time invalid")
        if (
            not isinstance(citation.supports, tuple)
            or not 0 < len(citation.supports) <= 50
        ):
            raise ValueError("citation supports invalid")
        supports = tuple(
            cls._text(value, "support", 128) for value in citation.supports
        )
        return CitationInput(
            citation_key=key,
            title=title,
            url=canonical_url,
            site=site,
            retrieved_at=citation.retrieved_at,
            supports=supports,
        )

    def record(
        self,
        conversation_id: UUID,
        message_id: UUID,
        citations: tuple[CitationInput, ...],
    ) -> tuple[CitationInput, ...]:
        if (
            not isinstance(conversation_id, UUID)
            or not isinstance(message_id, UUID)
            or not isinstance(citations, tuple)
            or len(citations) > 50
        ):
            raise ValueError("citation collection invalid")
        normalized = tuple(self._normalize(citation) for citation in citations)
        keys = tuple(citation.citation_key for citation in normalized)
        if len(set(keys)) != len(keys):
            raise ValueError("citation keys must be unique")
        if not normalized:
            return ()
        return tuple(self._repository.record(conversation_id, message_id, normalized))
