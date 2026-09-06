from __future__ import annotations

import asyncio
import hashlib
import html
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pypdf import PdfReader

from .evidence import EvidenceArchive, EvidencePayload, EvidenceRecord
from .models import canonical_panorama_url

SourceType = Literal[
    "official_product",
    "official_solution",
    "official_news",
    "official_financial",
    "official_campus",
    "patent_or_paper",
    "reviewed_industry_interview",
]
TrustTier = Literal["primary", "secondary"]

_SOURCE_TYPES = frozenset(
    {
        "official_product",
        "official_solution",
        "official_news",
        "official_financial",
        "official_campus",
        "patent_or_paper",
        "reviewed_industry_interview",
    }
)
_TRUST_TIERS = frozenset({"primary", "secondary"})
_COMPANY_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_CONTROL_TEXT = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"assistant\s*:|忽略(?:以上|之前|此前).*指令|系统提示|开发者消息)",
    re.IGNORECASE,
)
_APPROVED_COMPANIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "union-optech": (
        "https://www.union-optech.com",
        ("联合光电", "union optech"),
    ),
    "robosense": ("https://www.robosense.ai", ("速腾聚创", "robosense")),
    "hesai": ("https://www.hesaitech.com", ("禾赛", "hesai")),
    "bambu-lab": ("https://bambulab.com", ("拓竹", "bambu lab", "bambu")),
    "creality": ("https://www.creality.cn", ("创想三维", "creality")),
    "elegoo": ("https://www.elegoo.com.cn", ("智能派", "elegoo")),
    "revopoint": ("https://www.revopoint3d.com", ("知象光电", "revopoint")),
    "shining3d": ("https://www.shining3d.com", ("先临三维", "shining 3d")),
    "scantech": ("https://www.3d-scantech.com.cn", ("思看科技", "scantech")),
    "agibot": ("https://www.zhiyuan-robot.com", ("智元", "agibot")),
    "insta360": ("https://www.insta360.com", ("影石", "insta360")),
    "huawei": ("https://www.huawei.com/cn", ("华为", "huawei")),
}
_SKIPPED_TAGS = frozenset(
    {"script", "style", "nav", "form", "noscript", "footer", "svg", "canvas"}
)


class PublicDocumentCollectionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        evidence: EvidenceRecord | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.evidence = evidence
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class PublicDocumentTarget:
    company_key: str
    source_type: SourceType
    source_url: str
    trust_tier: TrustTier

    def __post_init__(self) -> None:
        company_key = self.company_key.strip() if isinstance(self.company_key, str) else ""
        if _COMPANY_KEY.fullmatch(company_key) is None or company_key not in _APPROVED_COMPANIES:
            raise ValueError("public document company invalid")
        if self.source_type not in _SOURCE_TYPES or self.trust_tier not in _TRUST_TIERS:
            raise ValueError("public document type invalid")
        source_url = canonical_panorama_url(self.source_url)
        if not _is_approved_url(company_key, source_url):
            raise ValueError("public document source not approved")
        object.__setattr__(self, "company_key", company_key)
        object.__setattr__(self, "source_url", source_url)


@dataclass(frozen=True, slots=True)
class PublicIntelligenceDocument:
    document_id: UUID
    company_key: str
    source_type: SourceType
    source_url: str
    title: str
    text_excerpt: str
    evidence_sha256: str
    text_sha256: str
    observed_at: datetime
    trust_tier: TrustTier

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, UUID):
            raise TypeError("public document identity invalid")
        if self.company_key not in _APPROVED_COMPANIES:
            raise ValueError("public document company invalid")
        if self.source_type not in _SOURCE_TYPES or self.trust_tier not in _TRUST_TIERS:
            raise ValueError("public document type invalid")
        source_url = canonical_panorama_url(self.source_url)
        if not _is_approved_url(self.company_key, source_url):
            raise ValueError("public document source not approved")
        title = self.title.strip() if isinstance(self.title, str) else ""
        text = self.text_excerpt.strip() if isinstance(self.text_excerpt, str) else ""
        if not 1 <= len(title) <= 1000 or not 1 <= len(text) <= 65536:
            raise ValueError("public document text invalid")
        if (
            _SHA256.fullmatch(self.evidence_sha256) is None
            or _SHA256.fullmatch(self.text_sha256) is None
            or hashlib.sha256(text.encode()).hexdigest() != self.text_sha256
        ):
            raise ValueError("public document checksum invalid")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("public document observation invalid")
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "text_excerpt", text)

    def as_dict(self) -> dict[str, str]:
        return {
            "document_id": str(self.document_id),
            "company_key": self.company_key,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "title": self.title,
            "text_excerpt": self.text_excerpt,
            "evidence_sha256": self.evidence_sha256,
            "text_sha256": self.text_sha256,
            "observed_at": self.observed_at.isoformat(),
            "trust_tier": self.trust_tier,
        }


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname or "", parsed.port or 443


def _is_approved_url(company_key: str, url: str) -> bool:
    root = _APPROVED_COMPANIES[company_key][0]
    return _origin(url) == _origin(root)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skipped_depth = 0
        self._inside_title = False
        self.parts: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        selected = tag.casefold()
        if selected in _SKIPPED_TAGS:
            self._skipped_depth += 1
        if selected == "title":
            self._inside_title = True

    def handle_endtag(self, tag: str) -> None:
        selected = tag.casefold()
        if selected in _SKIPPED_TAGS and self._skipped_depth:
            self._skipped_depth -= 1
        if selected == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:
        selected = " ".join(html.unescape(data).split())
        if not selected or self._skipped_depth:
            return
        if self._inside_title:
            self.title_parts.append(selected)
        self.parts.append(selected)


def _clean_lines(values: list[str]) -> str:
    selected = []
    for value in values:
        line = " ".join(value.replace("\x00", " ").split())
        if line and not _CONTROL_TEXT.search(line):
            selected.append(line)
    return "\n".join(dict.fromkeys(selected))[:65536].strip()


def _extract_html(body: bytes) -> tuple[str, str]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
    parser = _VisibleTextParser()
    parser.feed(text)
    title = _clean_lines(parser.title_parts) or "Official company document"
    excerpt = _clean_lines(parser.parts)
    if not excerpt:
        raise PublicDocumentCollectionError("document_text_unavailable")
    return title[:1000], excerpt


def _extract_pdf(body: bytes) -> tuple[str, str]:
    try:
        reader = PdfReader(io.BytesIO(body))
        metadata_title = str((reader.metadata or {}).get("/Title") or "").strip()
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:
        raise PublicDocumentCollectionError("document_pdf_invalid") from error
    excerpt = _clean_lines(pages)
    if not excerpt:
        raise PublicDocumentCollectionError("document_text_unavailable")
    return (metadata_title or "Official company PDF")[:1000], excerpt


async def _read_bounded(response: httpx.Response, maximum_bytes: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > maximum_bytes:
        raise PublicDocumentCollectionError("response_too_large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes(chunk_size=min(65536, maximum_bytes + 1)):
        size += len(chunk)
        if size > maximum_bytes:
            raise PublicDocumentCollectionError("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def collect_public_document(
    target: PublicDocumentTarget,
    *,
    archive: EvidenceArchive,
    client: httpx.AsyncClient,
    maximum_response_bytes: int = 10 * 1024 * 1024,
    total_timeout_seconds: float = 45,
) -> PublicIntelligenceDocument:
    if not isinstance(target, PublicDocumentTarget):
        raise TypeError("public document target required")
    if not isinstance(archive, EvidenceArchive) or not isinstance(client, httpx.AsyncClient):
        raise TypeError("public document collector dependency invalid")
    current = target.source_url
    try:
        async with asyncio.timeout(total_timeout_seconds):
            for redirect_number in range(2):
                async with client.stream(
                    "GET",
                    current,
                    follow_redirects=False,
                    timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
                    headers={"Accept": "text/html,application/pdf"},
                ) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if redirect_number or not location:
                            raise PublicDocumentCollectionError("redirect_not_approved")
                        redirected = canonical_panorama_url(urljoin(current, location))
                        if not _is_approved_url(target.company_key, redirected):
                            raise PublicDocumentCollectionError("redirect_not_approved")
                        current = redirected
                        continue
                    if response.status_code >= 500:
                        raise PublicDocumentCollectionError(
                            "source_unavailable", retryable=True
                        )
                    if response.status_code >= 400:
                        raise PublicDocumentCollectionError("source_rejected")
                    body = await _read_bounded(response, maximum_response_bytes)
                    mime = response.headers.get(
                        "content-type", "application/octet-stream"
                    )[:255]
                    break
            else:
                raise PublicDocumentCollectionError("redirect_not_approved")
    except TimeoutError:
        raise PublicDocumentCollectionError("source_timeout", retryable=True) from None
    except (httpx.TimeoutException, httpx.NetworkError):
        raise PublicDocumentCollectionError("source_unavailable", retryable=True) from None

    observed_at = datetime.now(timezone.utc)
    evidence = archive.store(
        EvidencePayload(
            source_url=current,
            mime=mime,
            body=body,
            response_headers=dict(response.headers),
        )
    )
    try:
        title, excerpt = (
            _extract_pdf(body)
            if "pdf" in mime.casefold() or body.startswith(b"%PDF-")
            else _extract_html(body)
        )
    except PublicDocumentCollectionError as error:
        raise PublicDocumentCollectionError(
            error.code, evidence=evidence, retryable=error.retryable
        ) from None
    identity_text = f"{title}\n{excerpt}".casefold()
    if not any(
        marker.casefold() in identity_text
        for marker in _APPROVED_COMPANIES[target.company_key][1]
    ):
        raise PublicDocumentCollectionError(
            "source_identity_mismatch", evidence=evidence
        )
    text_sha256 = hashlib.sha256(excerpt.encode()).hexdigest()
    document_id = uuid5(
        NAMESPACE_URL,
        f"orbbec:hr-intelligence:document:{target.company_key}:"
        f"{target.source_type}:{current}:{evidence.sha256}",
    )
    return PublicIntelligenceDocument(
        document_id=document_id,
        company_key=target.company_key,
        source_type=target.source_type,
        source_url=current,
        title=title,
        text_excerpt=excerpt,
        evidence_sha256=evidence.sha256,
        text_sha256=text_sha256,
        observed_at=observed_at,
        trust_tier=target.trust_tier,
    )


__all__ = [
    "PublicDocumentCollectionError",
    "PublicDocumentTarget",
    "PublicIntelligenceDocument",
    "collect_public_document",
]
