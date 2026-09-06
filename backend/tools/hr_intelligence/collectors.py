from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx

from .evidence import EvidenceArchive, EvidencePayload, EvidenceRecord
from .models import canonical_panorama_url

_NON_NATIVE_IPV6_PREFIXES = tuple(
    ipaddress.ip_network(value)
    for value in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


def _system_resolver(hostname: str, port: int) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item[4][0]
            for item in socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        )
    )


def _is_public_unicast(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and any(
        address in prefix for prefix in _NON_NATIVE_IPV6_PREFIXES
    ):
        return False
    candidates = [address]
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        candidates.append(address.ipv4_mapped)
    return all(
        candidate.is_global
        and not candidate.is_multicast
        and not candidate.is_unspecified
        and not candidate.is_loopback
        and not candidate.is_link_local
        and not candidate.is_private
        and not candidate.is_reserved
        for candidate in candidates
    )


def validate_panorama_destination(
    raw: str,
    *,
    resolver: Callable[[str, int], tuple[str, ...] | list[str]] = _system_resolver,
) -> str:
    try:
        selected = canonical_panorama_url(raw)
        hostname = urlsplit(selected).hostname
    except ValueError:
        raise ValueError("panorama destination invalid") from None
    if hostname is None:
        raise ValueError("panorama destination invalid")
    try:
        addresses = tuple(resolver(hostname, 443))
        parsed_addresses = tuple(ipaddress.ip_address(value) for value in addresses)
    except Exception:  # noqa: BLE001 - injected DNS failures must fail closed
        raise ValueError("panorama destination invalid") from None
    if not parsed_addresses or any(
        not _is_public_unicast(address) for address in parsed_addresses
    ):
        raise ValueError("panorama destination invalid")
    return selected


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


class _SyntheticEnvelope:
    def __init__(self, maximum_bytes: int, source_url: str) -> None:
        self.jobs: list[object] = []
        self.pages: list[str] = []
        self._maximum_bytes = maximum_bytes
        self._estimated_bytes = 128 + len(source_url.encode("utf-8"))

    @staticmethod
    def _size(value: object) -> int:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def _append(self, values: list[object] | list[str], value: object) -> None:
        selected_size = self._size(value) + 1
        if self._estimated_bytes + selected_size > self._maximum_bytes:
            raise CollectionError("response_too_large")
        values.append(value)  # type: ignore[arg-type]
        self._estimated_bytes += selected_size

    def add_page(self, value: str) -> None:
        self._append(self.pages, value)

    def add_job(self, value: object) -> None:
        self._append(self.jobs, value)

    def payload(self, document: Mapping[str, object]) -> bytes:
        body = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        if len(body) > self._maximum_bytes:
            raise CollectionError("response_too_large")
        return body


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
        regional_parts = [
            _plain(value.get(key), "") for key in ("province", "city", "area")
        ]
        if any(regional_parts):
            return "·".join(part for part in regional_parts if part)
        address = value.get("address", value)
        if isinstance(address, Mapping):
            parts = [
                _plain(address.get(key), "")
                for key in ("addressRegion", "addressLocality", "streetAddress")
            ]
            if not any(parts):
                parts = [
                    _plain(address.get(key), "")
                    for key in ("province", "city", "area", "address")
                ]
            return "·".join(part for part in parts if part) or "未公开"
        if isinstance(address, str):
            return _plain(address)
    return _plain(value)


def _split_job_text(value: object) -> tuple[str, str]:
    text = _plain(value)
    for marker in ("任职要求：", "任职要求", "岗位要求：", "岗位要求"):
        if marker in text:
            duty, requirement = text.split(marker, 1)
            duty = re.sub(r"^(?:一、)?(?:工作|岗位)?职责[：:]?", "", duty).strip()
            requirement = re.sub(r"^(?:二、)?", "", requirement).strip()
            return duty or "未公开", requirement or "未公开"
    return text, "未公开"


def _without_html_comments(value: str) -> str:
    return re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)


def _states_no_open_jobs(value: str) -> bool:
    return (
        re.search(
            r"暂无(?:招聘|职位|岗位)|暂无相关职位|没有(?:招聘|职位|岗位)|无在招岗位",
            _plain(value, ""),
        )
        is not None
    )


def _job_from_mapping(
    item: Mapping[str, object], target: SourceTarget
) -> NormalizedPublicJob:
    title = _plain(
        item.get("title")
        or item.get("name")
        or item.get("JobAdName")
        or item.get("job_name"),
        "",
    )
    if not title:
        raise ValueError("job title unavailable")
    location = _location(
        item.get("jobLocation")
        or item.get("location")
        or item.get("locations")
        or item.get("city_list")
        or item.get("city_info")
        or item.get("LocNames")
        or item.get("city_name")
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
    description = (
        item.get("description")
        or item.get("responsibilities")
        or item.get("job_descript")
    )
    description_duty, description_requirement = _split_job_text(description)
    return NormalizedPublicJob(
        public_job_key=_identifier(
            item.get("identifier")
            or item.get("id")
            or item.get("jobId")
            or item.get("JobAdId")
            or item.get("Id")
            or item.get("publish_id"),
            title=title,
            location=location,
            source_url=source_url,
        ),
        title=title,
        location=location,
        duty_excerpt=_plain(item.get("duty") or item.get("Duty") or description_duty),
        requirement_excerpt=_plain(
            item.get("qualifications")
            or item.get("requirements")
            or item.get("requirement")
            or item.get("Require")
            or item.get("experienceRequirements")
            or item.get("job_require")
            or description_requirement
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
        "career",
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
            if "vnd.orbbec.hr-panorama+json" in mime.lower() and isinstance(
                decoded.get("_collection_error"), str
            ):
                raise CollectionError(
                    decoded["_collection_error"],
                    retryable=decoded.get("_collection_error_retryable") is True,
                )
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
            moka_jobs = decoded.get("jobs")
            if isinstance(moka_jobs, list) and "total" in decoded:
                total = decoded.get("total")
                if decoded.get("code") not in {None, 0}:
                    raise CollectionError("source_rejected")
                if (
                    isinstance(total, bool)
                    or not isinstance(total, int)
                    or total < len(moka_jobs)
                ):
                    raise CollectionError("unsupported_schema")
                if total > len(moka_jobs):
                    raise CollectionError("response_truncated")
                if total == 0:
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
        parsed_url = urlsplit(target.source_url)
        if (
            (parsed_url.hostname or "").endswith(".bysjy.com.cn")
            and parsed_url.path == "/detail/career"
        ):
            marker = "var data = JSON.parse(JSON.stringify("
            start = text.find(marker)
            if start >= 0:
                try:
                    embedded, _ = json.JSONDecoder().raw_decode(
                        text[start + len(marker) :].lstrip()
                    )
                except json.JSONDecodeError:
                    pass
                else:
                    payloads.append(embedded)
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
        path = urlsplit(target.source_url).path
        if (
            hostname == "app.mokahr.com"
            or "dingtalkcloud.com" in hostname
            or "dingtalkoxm.com" in hostname
        ) and re.search(r"/(?:social|campus)-recruitment/[^/]+/\d+", path):
            return await self._fetch_moka(target)
        if hostname == "www.elegoo.com.cn" and path.endswith("/join/index.html"):
            return await self._fetch_elegoo(target)
        if hostname == "hr.revopoint3d.com.cn" and (
            path in {"", "/"} or re.fullmatch(r"/gwtd1?\.html", path)
        ):
            return await self._fetch_revopoint(target)
        if hostname == "career.huawei.com" and path in {
            "/reccampportal/portal5/social-recruitment.html",
            "/reccampportal/portal5/campus-recruitment.html",
        }:
            return await self._fetch_huawei_current(target)
        if hostname == "career.huawei.com" and path.endswith(
            "/huawei-special-recruitment.html"
        ):
            return await self._fetch_huawei(target)
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
                return response, current, await self._read_bounded(response)
        raise CollectionError("redirect_not_approved")

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > self._maximum_response_bytes
        ):
            raise CollectionError("response_too_large")
        chunks: list[bytes] = []
        size = 0
        chunk_size = min(64 * 1024, self._maximum_response_bytes + 1)
        async for chunk in response.aiter_bytes(chunk_size=chunk_size):
            size += len(chunk)
            if size > self._maximum_response_bytes:
                raise CollectionError("response_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _get_derived(
        self, url: str, *, referer: str | None = None
    ) -> tuple[httpx.Response, bytes]:
        selected = canonical_panorama_url(self._destination_validator(url))
        headers = {
            "Accept": "application/json, text/javascript, text/html",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            ),
        }
        if referer:
            headers["Referer"] = referer
        async with self._client.stream(
            "GET",
            selected,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
            headers=headers,
        ) as response:
            if 300 <= response.status_code < 400:
                raise CollectionError("redirect_not_approved")
            if response.status_code >= 500:
                raise CollectionError("source_unavailable", retryable=True)
            if response.status_code >= 400:
                raise CollectionError("source_rejected")
            return response, await self._read_bounded(response)

    @staticmethod
    def _synthetic_response(body: bytes) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/vnd.orbbec.hr-panorama+json"},
        )

    async def _fetch_moka(
        self, target: SourceTarget
    ) -> tuple[httpx.Response, str, bytes]:
        matched = re.search(
            r"/(social|campus)-recruitment/([^/]+)/(\d+)",
            urlsplit(target.source_url).path,
        )
        if matched is None:
            raise CollectionError("unsupported_schema")
        mode, org_id, site_id = matched.groups()
        envelope = _SyntheticEnvelope(self._maximum_response_bytes, target.source_url)
        seen_job_keys: set[str] = set()
        total: int | None = None
        offset = 0
        while total is None or offset < total:
            endpoint = (
                f"https://api.mokahr.com/api-platform/v1/jobs/{org_id}"
                f"?mode={mode}&limit=100&offset={offset}&siteId={site_id}"
            )
            _response, raw = await self._get_derived(endpoint)
            try:
                page = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise CollectionError("unsupported_schema") from None
            page_jobs = page.get("jobs") if isinstance(page, Mapping) else None
            page_total = page.get("total") if isinstance(page, Mapping) else None
            if (
                not isinstance(page, Mapping)
                or page.get("code") != 0
                or not isinstance(page_jobs, list)
                or isinstance(page_total, bool)
                or not isinstance(page_total, int)
                or page_total < 0
            ):
                raise CollectionError("source_rejected")
            if total is not None and total != page_total:
                raise CollectionError(
                    "source_changed_during_collection", retryable=True
                )
            total = page_total
            envelope.add_page(raw.decode("utf-8-sig"))
            for item in page_jobs:
                if not isinstance(item, Mapping):
                    return self._synthetic_failure(
                        target, envelope, "unsupported_schema"
                    )
                try:
                    public_job_key = _job_from_mapping(item, target).public_job_key
                except ValueError:
                    return self._synthetic_failure(
                        target, envelope, "unsupported_schema"
                    )
                if public_job_key in seen_job_keys:
                    return self._synthetic_failure(
                        target,
                        envelope,
                        "source_changed_during_collection",
                        retryable=True,
                    )
                seen_job_keys.add(public_job_key)
                envelope.add_job(item)
            if not page_jobs:
                if offset < total:
                    raise CollectionError("response_truncated")
                break
            offset += len(page_jobs)
            if len(envelope.jobs) > 10000:
                raise CollectionError("response_too_large")
        if total is None or len(envelope.jobs) != total or len(seen_job_keys) != total:
            return self._synthetic_failure(target, envelope, "response_truncated")
        payload = envelope.payload(
            {
                "code": 0,
                "total": total or 0,
                "jobs": envelope.jobs,
                "_evidence_pages": envelope.pages,
                "_source_url": target.source_url,
            }
        )
        return self._synthetic_response(payload), target.source_url, payload

    async def _fetch_elegoo(
        self, target: SourceTarget
    ) -> tuple[httpx.Response, str, bytes]:
        _response, listing = await self._get_derived(target.source_url)
        text = listing.decode("utf-8-sig")
        active_text = _without_html_comments(text)
        envelope = _SyntheticEnvelope(self._maximum_response_bytes, target.source_url)
        envelope.add_page(text)
        paths = tuple(
            dict.fromkeys(
                re.findall(
                    r'href=["\']([^"\']*/index/join/detail/id/\d+\.html)["\']',
                    active_text,
                )
            )
        )
        if not paths:
            return self._synthetic_jobs(
                target,
                envelope,
                verified_empty=_states_no_open_jobs(active_text),
            )
        if len(paths) > 100:
            return self._synthetic_failure(target, envelope, "response_truncated")
        target_origin = urlsplit(target.source_url)
        detail_urls: list[str] = []
        for path in paths:
            detail_url = canonical_panorama_url(urljoin(target.source_url, path))
            detail_origin = urlsplit(detail_url)
            if (
                detail_origin.scheme,
                detail_origin.hostname,
                detail_origin.port or 443,
            ) != (
                target_origin.scheme,
                target_origin.hostname,
                target_origin.port or 443,
            ):
                continue
            detail_urls.append(detail_url)
        if not detail_urls:
            return self._synthetic_failure(target, envelope, "unsupported_schema")
        for detail_url in detail_urls:
            _detail_response, raw = await self._get_derived(detail_url)
            detail = raw.decode("utf-8-sig")
            envelope.add_page(detail)
            title_match = re.search(
                r'class=["\'][^"\']*xqtitle[^"\']*["\'][^>]*>.*?<h2>(.*?)</h2>',
                detail,
                re.IGNORECASE | re.DOTALL,
            )
            body_match = re.search(
                r'class=["\'][^"\']*zpxq[^"\']*["\'][^>]*>(.*?)</div>',
                detail,
                re.IGNORECASE | re.DOTALL,
            )
            if title_match is None or body_match is None:
                return self._synthetic_failure(target, envelope, "unsupported_schema")
            duty, requirement = _split_job_text(body_match.group(1))
            envelope.add_job(
                {
                    "id": re.search(r"/id/(\d+)\.html", detail_url).group(1),
                    "title": _plain(title_match.group(1)),
                    "location": "未公开",
                    "duty": duty,
                    "requirements": requirement,
                    "url": detail_url,
                    "status": "open",
                }
            )
        return self._synthetic_jobs(target, envelope)

    async def _fetch_revopoint(
        self, target: SourceTarget
    ) -> tuple[httpx.Response, str, bytes]:
        selected_path = urlsplit(target.source_url).path
        initial_urls = (
            (
                "https://hr.revopoint3d.com.cn/gwtd.html",
                "https://hr.revopoint3d.com.cn/gwtd1.html",
            )
            if selected_path in {"", "/"}
            else (target.source_url,)
        )
        envelope = _SyntheticEnvelope(self._maximum_response_bytes, target.source_url)
        listing_pages: list[tuple[str, str]] = []
        for initial_url in initial_urls:
            _response, first = await self._get_derived(initial_url)
            first_text = first.decode("utf-8-sig")
            envelope.add_page(first_text)
            listing_pages.append((initial_url, first_text))
            base_name = urlsplit(initial_url).path.rsplit(".", 1)[0].lstrip("/")
            active_text = _without_html_comments(first_text)
            page_paths = tuple(
                dict.fromkeys(
                    re.findall(
                        rf'href=["\'](/?{re.escape(base_name)}-\d+\.html)["\']',
                        active_text,
                    )
                )
            )
            if len(page_paths) > 20:
                return self._synthetic_failure(target, envelope, "response_truncated")
            for path in page_paths:
                _page_response, raw = await self._get_derived(
                    canonical_panorama_url(urljoin(initial_url, path))
                )
                page_url = canonical_panorama_url(urljoin(initial_url, path))
                page_text = raw.decode("utf-8-sig")
                envelope.add_page(page_text)
                listing_pages.append((page_url, page_text))
        for listing_url, listing in listing_pages:
            active_listing = _without_html_comments(listing)
            for row in re.findall(
                r"<tr[^>]*>(.*?)</tr>",
                active_listing,
                re.IGNORECASE | re.DOTALL,
            ):
                job_id = re.search(r"my_load_more\((\d+)\)", row)
                cells = re.findall(
                    r"<td[^>]*>(.*?)</td>", row, re.IGNORECASE | re.DOTALL
                )
                if job_id is None or len(cells) < 3:
                    continue
                detail_url = (
                    "https://hr.revopoint3d.com.cn/index.php?s=api&c=api&m=template"
                    "&name=content_data.html&module=join&catid=4&resume_count=0"
                    f"&format=json&id={job_id.group(1)}"
                )
                _detail_response, raw = await self._get_derived(detail_url)
                envelope.add_page(raw.decode("utf-8-sig"))
                try:
                    detail_payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return self._synthetic_failure(
                        target, envelope, "unsupported_schema"
                    )
                if not isinstance(detail_payload, Mapping) or not isinstance(
                    detail_payload.get("msg"), str
                ):
                    return self._synthetic_failure(
                        target, envelope, "unsupported_schema"
                    )
                duty, requirement = _split_job_text(detail_payload.get("msg"))
                envelope.add_job(
                    {
                        "id": job_id.group(1),
                        "title": _plain(cells[0]),
                        "location": _plain(cells[2]),
                        "duty": duty,
                        "requirements": requirement,
                        "url": listing_url,
                        "status": "open",
                    }
                )
        return self._synthetic_jobs(
            target,
            envelope,
            verified_empty=bool(listing_pages)
            and all(_states_no_open_jobs(page) for _, page in listing_pages),
        )

    async def _fetch_huawei_current(
        self, target: SourceTarget
    ) -> tuple[httpx.Response, str, bytes]:
        _response, landing = await self._get_derived(target.source_url)
        envelope = _SyntheticEnvelope(self._maximum_response_bytes, target.source_url)
        envelope.add_page(landing.decode("utf-8-sig"))
        campus = urlsplit(target.source_url).path.endswith("/campus-recruitment.html")
        total_rows: int | None = None
        total_pages: int | None = None
        seen_job_keys: set[str] = set()
        page_number = 1
        while total_pages is None or page_number <= total_pages:
            query = (
                "jobType=0&jobTypes=2&language=zh_CN&"
                "orderBy=ISS_STARTDATE_DESC_AND_IS_HOT_JOB"
                if campus
                else "jobType=1&orderBy=P_COUNT_DESC"
            )
            endpoint = (
                "https://career.huawei.com/reccampportal/services/portal/"
                f"portalpub/getJob/newHr/page/100/{page_number}?{query}"
            )
            _page_response, raw = await self._get_derived(
                endpoint, referer=target.source_url
            )
            envelope.add_page(raw.decode("utf-8-sig"))
            try:
                page = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._synthetic_failure(target, envelope, "unsupported_schema")
            page_info = page.get("pageVO") if isinstance(page, Mapping) else None
            page_jobs = page.get("result") if isinstance(page, Mapping) else None
            selected_total = (
                page_info.get("totalRows") if isinstance(page_info, Mapping) else None
            )
            selected_pages = (
                page_info.get("totalPages") if isinstance(page_info, Mapping) else None
            )
            selected_page = (
                page_info.get("curPage") if isinstance(page_info, Mapping) else None
            )
            if (
                not isinstance(page_jobs, list)
                or isinstance(selected_total, bool)
                or not isinstance(selected_total, int)
                or selected_total < 0
                or isinstance(selected_pages, bool)
                or not isinstance(selected_pages, int)
                or selected_pages < 0
                or selected_page != page_number
                or (total_rows is not None and total_rows != selected_total)
                or (total_pages is not None and total_pages != selected_pages)
            ):
                return self._synthetic_failure(
                    target, envelope, "source_changed_during_collection", retryable=True
                )
            total_rows = selected_total
            total_pages = selected_pages
            if not page_jobs and page_number <= total_pages:
                return self._synthetic_failure(target, envelope, "response_truncated")
            for item in page_jobs:
                if not isinstance(item, Mapping):
                    return self._synthetic_failure(
                        target, envelope, "unsupported_schema"
                    )
                public_job_key = _plain(item.get("jobId"), "")
                title = _plain(item.get("jobname") or item.get("externalJobName"), "")
                if not public_job_key or not title or public_job_key in seen_job_keys:
                    return self._synthetic_failure(
                        target,
                        envelope,
                        "source_changed_during_collection",
                        retryable=True,
                    )
                seen_job_keys.add(public_job_key)
                envelope.add_job(
                    {
                        "id": public_job_key,
                        "title": title,
                        "location": item.get("jobArea")
                        or item.get("jobAddress")
                        or "未公开",
                        "duty": item.get("mainBusiness") or "未公开",
                        "requirements": item.get("jobRequire") or "未公开",
                        "url": target.source_url,
                        "status": "open",
                    }
                )
            if len(envelope.jobs) > 10000:
                raise CollectionError("response_too_large")
            page_number += 1
        if total_rows is None or len(envelope.jobs) != total_rows:
            return self._synthetic_failure(target, envelope, "response_truncated")
        return self._synthetic_jobs(
            target,
            envelope,
            verified_empty=total_rows == 0,
        )

    async def _fetch_huawei(
        self, target: SourceTarget
    ) -> tuple[httpx.Response, str, bytes]:
        _response, landing = await self._get_derived(target.source_url)
        script_url = urljoin(target.source_url, "./js/postDatas.js")
        _script_response, raw = await self._get_derived(script_url)
        script = raw.decode("utf-8-sig")
        envelope = _SyntheticEnvelope(self._maximum_response_bytes, target.source_url)
        landing_text = landing.decode("utf-8-sig")
        envelope.add_page(landing_text)
        envelope.add_page(script)
        for block in re.findall(r"\{(.*?)\}(?:,|\s*\])", script, re.DOTALL):
            fields: dict[str, str] = {}
            for name in ("name", "type", "res", "int", "req", "link"):
                matched = re.search(rf"\b{name}\s*:\s*(['`])(.*?)\1", block, re.DOTALL)
                fields[name] = "" if matched is None else matched.group(2)
            job_id = re.search(r"[?&]jobId=(\d+)", fields["link"])
            if not fields["name"] or job_id is None:
                continue
            responsibility = " ".join(
                part
                for part in (
                    _plain(fields["type"], ""),
                    _plain(fields["res"], ""),
                    _plain(fields["int"], ""),
                )
                if part
            )
            envelope.add_job(
                {
                    "id": job_id.group(1),
                    "title": fields["name"],
                    "location": "多地",
                    "duty": responsibility or "职位方向以官方详情页为准",
                    "requirements": _plain(fields["req"]),
                    "url": urljoin(target.source_url, fields["link"]),
                    "status": "open",
                }
            )
        structural_empty = (
            re.search(r"\b__data_list\s*=\s*\[\s*\]\s*;?", script) is not None
        )
        return self._synthetic_jobs(
            target,
            envelope,
            verified_empty=_states_no_open_jobs(landing_text) and structural_empty,
        )

    def _synthetic_jobs(
        self,
        target: SourceTarget,
        envelope: _SyntheticEnvelope,
        *,
        verified_empty: bool = False,
    ) -> tuple[httpx.Response, str, bytes]:
        if not envelope.jobs and not verified_empty:
            return self._synthetic_failure(target, envelope, "unsupported_schema")
        payload = envelope.payload(
            {
                "code": 0,
                "total": len(envelope.jobs),
                "jobs": envelope.jobs,
                "_evidence_pages": envelope.pages,
            }
        )
        return self._synthetic_response(payload), target.source_url, payload

    def _synthetic_failure(
        self,
        target: SourceTarget,
        envelope: _SyntheticEnvelope,
        code: str,
        *,
        retryable: bool = False,
    ) -> tuple[httpx.Response, str, bytes]:
        payload = envelope.payload(
            {
                "jobs": envelope.jobs,
                "_evidence_pages": envelope.pages,
                "_collection_error": code,
                "_collection_error_retryable": retryable,
            }
        )
        return self._synthetic_response(payload), target.source_url, payload

    async def _fetch_feishu(
        self, target: SourceTarget, hostname: str
    ) -> tuple[httpx.Response, str, bytes]:
        path = urlsplit(target.source_url).path.strip("/").split("/", 1)[0]
        website_path = path or "index"
        endpoint = canonical_panorama_url(
            self._destination_validator(f"https://{hostname}/api/v1/search/job/posts")
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
