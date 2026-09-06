from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx

from .panorama_evidence import EvidenceArchive, EvidencePayload, EvidenceRecord
from .panorama_models import canonical_panorama_url
from .panorama_runtime import validate_panorama_destination


class CollectionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        evidence: EvidenceRecord | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.evidence = evidence
        self.observed_at = observed_at


def _approved(url: str, approved_urls: tuple[str, ...]) -> bool:
    return any(
        url == prefix
        or (
            "?" not in prefix
            and "#" not in prefix
            and (
                url.startswith(prefix)
                if prefix.endswith("/")
                else url.startswith(prefix + "/")
            )
        )
        for prefix in approved_urls
    )


@dataclass(frozen=True, slots=True)
class SourceTarget:
    source_id: UUID
    company_name: str
    source_url: str
    approved_urls: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, UUID):
            raise TypeError("source target invalid")
        company = (
            self.company_name.strip() if isinstance(self.company_name, str) else ""
        )
        if not 1 <= len(company) <= 500:
            raise ValueError("source target invalid")
        approved = tuple(canonical_panorama_url(value) for value in self.approved_urls)
        source_url = canonical_panorama_url(self.source_url)
        if not 1 <= len(approved) <= 20 or not _approved(source_url, approved):
            raise ValueError("source target invalid")
        object.__setattr__(self, "company_name", company)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "approved_urls", approved)


@dataclass(frozen=True, slots=True)
class NormalizedPublicJob:
    public_job_key: str
    title: str
    location: str
    duty_excerpt: str
    requirement_excerpt: str
    source_url: str
    status: str = "open"

    def __post_init__(self) -> None:
        for name, maximum in (
            ("public_job_key", 512),
            ("title", 1000),
            ("location", 1000),
            ("duty_excerpt", 32768),
            ("requirement_excerpt", 32768),
        ):
            value = getattr(self, name)
            value = value.strip() if isinstance(value, str) else ""
            if not value or len(value) > maximum:
                raise ValueError("normalized job invalid")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "source_url", canonical_panorama_url(self.source_url))
        if self.status not in {"open", "closed", "unknown"}:
            raise ValueError("normalized job invalid")


@dataclass(frozen=True, slots=True)
class CollectionResult:
    target: SourceTarget
    jobs: tuple[NormalizedPublicJob, ...]
    evidence: EvidenceRecord
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.target, SourceTarget)
            or not isinstance(self.jobs, tuple)
            or any(not isinstance(job, NormalizedPublicJob) for job in self.jobs)
            or not isinstance(self.evidence, EvidenceRecord)
            or self.observed_at.tzinfo is None
        ):
            raise ValueError("collection result invalid")

    @property
    def content_sha256(self) -> str:
        return self.evidence.sha256


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._inside = False
        self._parts: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "script" and "ld+json" in attributes.get("type", "").lower():
            self._inside = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._inside:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._inside:
            self.blocks.append("".join(self._parts))
            self._inside = False
            self._parts = []


def _plain(value: object, fallback: str = "未公开") -> str:
    if value is None:
        return fallback
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = " ".join(html.unescape(text).split())
    return text or fallback


def _identifier(value: object, *, title: str, location: str, source_url: str) -> str:
    if isinstance(value, Mapping):
        value = value.get("value") or value.get("name")
    selected = _plain(value, "")
    if selected:
        return selected[:512]
    return hashlib.sha256(f"{title}\n{location}\n{source_url}".encode()).hexdigest()


def _location(value: object) -> str:
    if isinstance(value, list):
        return " / ".join(dict.fromkeys(_location(item) for item in value))
    if isinstance(value, Mapping):
        for key in ("name", "cn_name", "zh_name", "i18n_name"):
            named = _plain(value.get(key), "")
            if named:
                return named
        address = value.get("address", value)
        if isinstance(address, Mapping):
            parts = [
                _plain(address.get(key), "")
                for key in ("addressRegion", "addressLocality", "streetAddress")
            ]
            return "·".join(part for part in parts if part) or "未公开"
    return _plain(value)


def _job_from_mapping(
    item: Mapping[str, object], target: SourceTarget
) -> NormalizedPublicJob:
    title = _plain(
        item.get("title") or item.get("name") or item.get("JobAdName"), ""
    )
    if not title:
        raise ValueError("job title unavailable")
    location = _location(
        item.get("jobLocation")
        or item.get("location")
        or item.get("city_list")
        or item.get("city_info")
        or item.get("LocNames")
    )
    raw_url = item.get("url")
    source_url = target.source_url
    if isinstance(raw_url, str) and raw_url.strip():
        candidate = canonical_panorama_url(urljoin(target.source_url, raw_url.strip()))
        if _approved(candidate, target.approved_urls):
            source_url = candidate
    elif urlsplit(target.source_url).hostname.endswith(".jobs.feishu.cn"):
        job_id = _plain(item.get("id"), "")
        if job_id:
            base = target.source_url.rstrip("/")
            candidate = canonical_panorama_url(f"{base}/position/{job_id}/detail")
            if _approved(candidate, target.approved_urls):
                source_url = candidate
    status_value = str(item.get("status", item.get("Status", "open"))).lower()
    if "JobAdId" in item:
        status_value = "open"
    status = (
        status_value if status_value in {"open", "closed", "unknown"} else "unknown"
    )
    return NormalizedPublicJob(
        public_job_key=_identifier(
            item.get("identifier")
            or item.get("id")
            or item.get("jobId")
            or item.get("JobAdId")
            or item.get("Id"),
            title=title,
            location=location,
            source_url=source_url,
        ),
        title=title,
        location=location,
        duty_excerpt=_plain(
            item.get("description")
            or item.get("responsibilities")
            or item.get("duty")
            or item.get("Duty")
        ),
        requirement_excerpt=_plain(
            item.get("qualifications")
            or item.get("requirements")
            or item.get("requirement")
            or item.get("Require")
            or item.get("experienceRequirements")
        ),
        source_url=source_url,
        status=status,
    )


def _candidate_mappings(value: object) -> list[Mapping[str, object]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    if value.get("@type") == "JobPosting":
        return [value]
    graph = value.get("@graph")
    if isinstance(graph, list):
        return [
            item
            for item in graph
            if isinstance(item, Mapping) and item.get("@type") == "JobPosting"
        ]
    for key in (
        "jobs",
        "positions",
        "data",
        "items",
        "results",
        "job_post_list",
        "Data",
    ):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, Mapping)]
        if isinstance(nested, Mapping):
            selected = _candidate_mappings(nested)
            if selected:
                return selected
    return []


def parse_public_jobs(
    body: bytes, mime: str, target: SourceTarget
) -> tuple[NormalizedPublicJob, ...]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CollectionError("response_encoding_invalid") from None
    payloads: list[object] = []
    if "json" in mime.lower() or text.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            raise CollectionError("unsupported_schema") from None
        if isinstance(decoded, Mapping):
            data = decoded.get("data")
            if isinstance(data, Mapping) and isinstance(
                data.get("job_post_list"), list
            ):
                count = data.get("count")
                returned = len(data["job_post_list"])
                if decoded.get("code") not in {None, 0}:
                    raise CollectionError("source_rejected")
                if (
                    isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < returned
                ):
                    raise CollectionError("unsupported_schema")
                if count > returned:
                    raise CollectionError("response_truncated")
                if count == 0:
                    return ()
            beisen_jobs = decoded.get("Data")
            if isinstance(beisen_jobs, list):
                count = decoded.get("Count")
                returned = len(beisen_jobs)
                if decoded.get("Code") not in {None, 0, 200}:
                    raise CollectionError("source_rejected")
                if (
                    isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < returned
                ):
                    raise CollectionError("unsupported_schema")
                if count > returned:
                    raise CollectionError("response_truncated")
                if count == 0:
                    return ()
        payloads.append(decoded)
    else:
        parser = _JsonLdParser()
        parser.feed(text)
        for block in parser.blocks:
            try:
                payloads.append(json.loads(block))
            except json.JSONDecodeError:
                continue
    jobs: list[NormalizedPublicJob] = []
    for payload in payloads:
        for item in _candidate_mappings(payload):
            try:
                jobs.append(_job_from_mapping(item, target))
            except ValueError:
                continue
    if not jobs:
        raise CollectionError("unsupported_schema")
    unique: dict[tuple[str, str], NormalizedPublicJob] = {}
    for job in jobs:
        unique.setdefault((job.public_job_key, job.source_url), job)
    return tuple(unique.values())


class PublicSourceCollector:
    def __init__(
        self,
        client: httpx.AsyncClient,
        archive: EvidenceArchive,
        *,
        maximum_response_bytes: int = 10 * 1024 * 1024,
        total_timeout_seconds: float = 45,
        destination_validator: Callable[[str], str] = validate_panorama_destination,
    ) -> None:
        if (
            not isinstance(client, httpx.AsyncClient)
            or not isinstance(archive, EvidenceArchive)
            or isinstance(maximum_response_bytes, bool)
            or not 1 <= maximum_response_bytes <= 10 * 1024 * 1024
            or not 1 <= total_timeout_seconds <= 120
            or not callable(destination_validator)
        ):
            raise ValueError("collector configuration invalid")
        self._client = client
        self._archive = archive
        self._maximum_response_bytes = maximum_response_bytes
        self._total_timeout_seconds = total_timeout_seconds
        self._destination_validator = destination_validator

    async def collect(self, target: SourceTarget) -> CollectionResult:
        if not isinstance(target, SourceTarget):
            raise TypeError("source target required")
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                response, final_url, body = await self._fetch(target)
        except TimeoutError:
            raise CollectionError("source_timeout", retryable=True) from None
        except (httpx.TimeoutException, httpx.NetworkError):
            raise CollectionError("source_unavailable", retryable=True) from None
        mime = response.headers.get("content-type", "application/octet-stream")[:255]
        observed_at = datetime.now(timezone.utc)
        evidence = self._archive.store(
            EvidencePayload(
                source_url=final_url,
                mime=mime,
                body=body,
                response_headers=dict(response.headers),
            )
        )
        try:
            jobs = parse_public_jobs(body, mime, target)
        except CollectionError as error:
            raise CollectionError(
                error.code,
                retryable=error.retryable,
                evidence=evidence,
                observed_at=observed_at,
            ) from None
        return CollectionResult(
            target=target,
            jobs=jobs,
            evidence=evidence,
            observed_at=observed_at,
        )

    async def _fetch(self, target: SourceTarget) -> tuple[httpx.Response, str, bytes]:
        hostname = urlsplit(target.source_url).hostname or ""
        if hostname.endswith(".jobs.feishu.cn"):
            return await self._fetch_feishu(target, hostname)
        if hostname.endswith(".zhiye.com"):
            return await self._fetch_beisen(target, hostname)
        current = target.source_url
        origin = urlsplit(current)
        for redirect_number in range(2):
            current = canonical_panorama_url(self._destination_validator(current))
            if not _approved(current, target.approved_urls):
                raise CollectionError("destination_not_approved")
            async with self._client.stream(
                "GET",
                current,
                follow_redirects=False,
                timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
                headers={"Accept": "application/ld+json, application/json, text/html"},
            ) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("location")
                    if redirect_number or not location:
                        raise CollectionError("redirect_not_approved")
                    redirected = canonical_panorama_url(urljoin(current, location))
                    candidate = urlsplit(redirected)
                    if (
                        candidate.scheme,
                        candidate.hostname,
                        candidate.port or 443,
                    ) != (
                        origin.scheme,
                        origin.hostname,
                        origin.port or 443,
                    ) or not _approved(redirected, target.approved_urls):
                        raise CollectionError("redirect_not_approved")
                    current = redirected
                    continue
                if response.status_code >= 500:
                    raise CollectionError("source_unavailable", retryable=True)
                if response.status_code >= 400:
                    raise CollectionError("source_rejected")
                content_length = response.headers.get("content-length")
                if (
                    content_length
                    and content_length.isdigit()
                    and int(content_length) > self._maximum_response_bytes
                ):
                    raise CollectionError("response_too_large")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._maximum_response_bytes:
                        raise CollectionError("response_too_large")
                    chunks.append(chunk)
                return response, current, b"".join(chunks)
        raise CollectionError("redirect_not_approved")

    async def _fetch_feishu(
        self, target: SourceTarget, hostname: str
    ) -> tuple[httpx.Response, str, bytes]:
        path = urlsplit(target.source_url).path.strip("/").split("/", 1)[0]
        website_path = path or "index"
        endpoint = canonical_panorama_url(
            self._destination_validator(
                f"https://{hostname}/api/v1/search/job/posts"
            )
        )
        parsed_endpoint = urlsplit(endpoint)
        parsed_target = urlsplit(target.source_url)
        if (
            parsed_endpoint.scheme,
            parsed_endpoint.hostname,
            parsed_endpoint.port or 443,
        ) != (
            parsed_target.scheme,
            parsed_target.hostname,
            parsed_target.port or 443,
        ):
            raise CollectionError("destination_not_approved")
        payload = {
            "keyword": "",
            "limit": 1000,
            "offset": 0,
            "portal_type": 2,
            "job_category_id_list": [],
            "location_code_list": [],
            "subject_id_list": [],
            "recruitment_id_list": [],
            "job_function_id_list": [],
        }
        async with self._client.stream(
            "POST",
            endpoint,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
            headers={
                "Accept": "application/json",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Content-Type": "application/json",
                "Origin": f"https://{hostname}",
                "Referer": target.source_url,
                "Portal-Channel": "office",
                "Portal-Platform": "pc",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                ),
                "website-path": website_path,
            },
            json=payload,
        ) as response:
            if 300 <= response.status_code < 400:
                raise CollectionError("redirect_not_approved")
            if response.status_code >= 500:
                raise CollectionError("source_unavailable", retryable=True)
            if response.status_code >= 400:
                raise CollectionError("source_rejected")
            content_length = response.headers.get("content-length")
            if (
                content_length
                and content_length.isdigit()
                and int(content_length) > self._maximum_response_bytes
            ):
                raise CollectionError("response_too_large")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._maximum_response_bytes:
                    raise CollectionError("response_too_large")
                chunks.append(chunk)
            return response, endpoint, b"".join(chunks)

    async def _fetch_beisen(
        self, target: SourceTarget, hostname: str
    ) -> tuple[httpx.Response, str, bytes]:
        endpoint = canonical_panorama_url(
            self._destination_validator(
                f"https://{hostname}/api/Jobad/GetJobAdPageList"
            )
        )
        parsed_endpoint = urlsplit(endpoint)
        parsed_target = urlsplit(target.source_url)
        if (
            parsed_endpoint.scheme,
            parsed_endpoint.hostname,
            parsed_endpoint.port or 443,
        ) != (
            parsed_target.scheme,
            parsed_target.hostname,
            parsed_target.port or 443,
        ):
            raise CollectionError("destination_not_approved")
        path = parsed_target.path.lower()
        payload = {
            "PageIndex": 0,
            "PageSize": 1000,
            "LocId": [],
            "Category": ["2" if "campus" in path else "1"],
            "KeyWords": "",
            "SpecialType": 0,
            "PortalId": "",
            "DisplayFields": [
                "Category",
                "Kind",
                "LocId",
                "PostDate",
                "Salary",
            ],
        }
        async with self._client.stream(
            "POST",
            endpoint,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
            headers={
                "Accept": "application/json",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Content-Type": "application/json",
                "Origin": f"https://{hostname}",
                "Referer": target.source_url,
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                ),
            },
            json=payload,
        ) as response:
            if 300 <= response.status_code < 400:
                raise CollectionError("redirect_not_approved")
            if response.status_code >= 500:
                raise CollectionError("source_unavailable", retryable=True)
            if response.status_code >= 400:
                raise CollectionError("source_rejected")
            content_length = response.headers.get("content-length")
            if (
                content_length
                and content_length.isdigit()
                and int(content_length) > self._maximum_response_bytes
            ):
                raise CollectionError("response_too_large")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._maximum_response_bytes:
                    raise CollectionError("response_too_large")
                chunks.append(chunk)
            return response, endpoint, b"".join(chunks)


__all__ = [
    "CollectionError",
    "CollectionResult",
    "NormalizedPublicJob",
    "PublicSourceCollector",
    "SourceTarget",
    "parse_public_jobs",
]
