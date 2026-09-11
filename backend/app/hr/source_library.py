"""Read an immutable, checksum-pinned HR source publication from server files."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from functools import cached_property
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .panorama_repository import PanoramaNotFound, PanoramaUnavailable

_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_COMPANY_KEYS = (
    "company_key",
    "name",
    "aliases",
    "job_count",
    "coverage_state",
    "document_state",
    "observed_at",
)
_SUMMARY_KEYS = (
    "job_id",
    "title",
    "location",
    "status",
    "source_url",
    "observed_at",
    "channel",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _public_company(value: Mapping[str, object]) -> dict[str, object]:
    return {key: value[key] for key in _COMPANY_KEYS}


class SourceLibrary:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(__file__).with_name("source_content")

    @cached_property
    def _loaded(self) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
        try:
            root = self.root.resolve()
            manifest = json.loads((root / "manifest.json").read_text("utf-8"))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
                raise ValueError("manifest invalid")
            unsigned = {
                key: value for key, value in manifest.items() if key != "edition"
            }
            expected_edition = (
                "source-" + hashlib.sha256(_canonical(unsigned)).hexdigest()[:20]
            )
            if manifest.get("edition") != expected_edition:
                raise ValueError("manifest identity mismatch")
            entries = manifest.get("companies")
            if not isinstance(entries, list) or not entries:
                raise ValueError("company manifest invalid")
            companies: dict[str, dict[str, object]] = {}
            all_job_ids: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise TypeError("company manifest invalid")
                company_key = entry.get("company_key")
                path_value = entry.get("path")
                if (
                    not isinstance(company_key, str)
                    or _KEY.fullmatch(company_key) is None
                    or company_key in companies
                    or not isinstance(path_value, str)
                    or path_value != f"{company_key}.json"
                    or _SHA256.fullmatch(str(entry.get("sha256"))) is None
                ):
                    raise ValueError("company manifest invalid")
                path = root / path_value
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("company path invalid")
                body = path.read_bytes()
                if (
                    len(body) != entry.get("size_bytes")
                    or hashlib.sha256(body).hexdigest() != entry["sha256"]
                ):
                    raise ValueError("company identity mismatch")
                value = json.loads(body)
                self._validate_company(value, entry, all_job_ids)
                companies[company_key] = value
            if (
                len(companies) != len(entries)
                or len(all_job_ids) != manifest.get("job_count")
                or sum(value["company"]["job_count"] for value in companies.values())
                != manifest.get("job_count")
            ):
                raise ValueError("publication count mismatch")
            return manifest, companies
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise PanoramaUnavailable("source publication unavailable") from None

    def _publication(self) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
        return self._loaded

    @staticmethod
    def _validate_company(
        value: object, entry: Mapping[str, object], all_job_ids: set[str]
    ) -> None:
        if not isinstance(value, dict) or not isinstance(value.get("company"), dict):
            raise TypeError("company content invalid")
        company = value["company"]
        if any(company.get(key) != entry.get(key) for key in _COMPANY_KEYS):
            raise ValueError("company metadata mismatch")
        jobs = value.get("jobs")
        if not isinstance(jobs, list) or len(jobs) != company.get("job_count"):
            raise ValueError("company job count mismatch")
        for name in ("documents", "channels", "limitations"):
            if not isinstance(value.get(name), list):
                raise TypeError("company content invalid")
        for job in jobs:
            if not isinstance(job, dict) or set(_SUMMARY_KEYS) - job.keys():
                raise ValueError("job invalid")
            job_id = job.get("job_id")
            if not isinstance(job_id, str) or job_id in all_job_ids:
                raise ValueError("job identity invalid")
            if job.get("source_kind") not in {"job", "archive"}:
                raise ValueError("job source kind invalid")
            if _SHA256.fullmatch(str(job.get("evidence_sha256"))) is None:
                raise ValueError("job evidence invalid")
            if not isinstance(job.get("fields"), list) or not isinstance(
                job.get("content_note"), str
            ):
                raise TypeError("job content invalid")
            all_job_ids.add(job_id)

    def catalog(self) -> dict[str, object]:
        manifest, _ = self._publication()
        return {
            "edition": manifest["edition"],
            "source_bundle_id": manifest["source_bundle_id"],
            "observed_at": manifest["observed_at"],
            "job_count": manifest["job_count"],
            "companies": [_public_company(item) for item in manifest["companies"]],
        }

    def company(
        self,
        company_key: str,
        edition: str,
        *,
        q: str | None = None,
        location: str | None = None,
        channel: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, object]:
        manifest, companies = self._publication()
        if edition != manifest["edition"] or company_key not in companies:
            raise PanoramaNotFound("source publication unavailable")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("source pagination invalid")
        value = companies[company_key]
        jobs = value["jobs"]
        query = q.strip().casefold() if isinstance(q, str) else None
        selected_location = location.strip() if isinstance(location, str) else None
        selected_channel = channel.strip() if isinstance(channel, str) else None
        if query:
            jobs = [
                job
                for job in jobs
                if query
                in "\n".join(
                    value
                    for value in (job["title"], job.get("duty"), job.get("requirement"))
                    if isinstance(value, str)
                ).casefold()
            ]
        if selected_location:
            jobs = [job for job in jobs if job["location"] == selected_location]
        if selected_channel:
            jobs = [job for job in jobs if job["channel"] == selected_channel]
        return {
            "edition": manifest["edition"],
            "company": _public_company(value["company"]),
            "documents": value["documents"],
            "channels": value["channels"],
            "limitations": value["limitations"],
            "locations": sorted(
                {job["location"] for job in value["jobs"] if job["location"]}
            ),
            "channel_options": list(
                dict.fromkeys(job["channel"] for job in value["jobs"])
            ),
            "items": [
                {key: job[key] for key in _SUMMARY_KEYS}
                for job in jobs[offset : offset + limit]
            ],
            "total": len(jobs),
            "offset": offset,
            "limit": limit,
        }

    def job(self, company_key: str, job_id: str, edition: str) -> dict[str, object]:
        manifest, companies = self._publication()
        if edition != manifest["edition"] or company_key not in companies:
            raise PanoramaNotFound("source publication unavailable")
        job = next(
            (
                item
                for item in companies[company_key]["jobs"]
                if item["job_id"] == job_id
            ),
            None,
        )
        if job is None:
            raise PanoramaNotFound("source job unavailable")
        return {**job, "edition": edition, "company_key": company_key}

    def resolve_references(
        self, markdown: str, source_bundle_id: str
    ) -> dict[str, object]:
        manifest, companies = self._publication()
        if source_bundle_id != manifest["source_bundle_id"]:
            raise PanoramaNotFound("source publication unavailable")
        hrefs = dict.fromkeys(
            re.findall(r"\]\(([^)\s]+)", markdown)
            + re.findall(r"https://[^\s)>`\]]+", markdown)
        )
        by_url: dict[str, list[tuple[str, Mapping[str, object]]]] = {}
        by_public_key: dict[str, list[tuple[str, Mapping[str, object]]]] = {}
        for company_key, company in companies.items():
            for job in company["jobs"]:
                by_url.setdefault(job["source_url"], []).append((company_key, job))
                by_public_key.setdefault(job["public_job_key"], []).append(
                    (company_key, job)
                )
        links: dict[str, dict[str, object]] = {}
        for href in hrefs:
            matches = by_url.get(href, [])
            matched_by_key = False
            if not matches:
                parsed = urlsplit(href)
                tokens = [part for part in parsed.path.split("/") if part]
                tokens.extend(value for _, value in parse_qsl(parsed.query))
                tokens.append(parsed.fragment)
                key_matches = {
                    (company_key, job["job_id"]): (company_key, job)
                    for token in tokens
                    for company_key, job in by_public_key.get(token, [])
                }
                matches = list(key_matches.values())
                matched_by_key = bool(matches)
            company_keys = {company_key for company_key, _ in matches}
            if len(company_keys) != 1:
                continue
            company_key = next(iter(company_keys))
            if len(matches) == 1 and (
                matched_by_key or matches[0][1]["source_kind"] == "job"
            ):
                links[href] = {
                    "company_key": company_key,
                    "job_id": matches[0][1]["job_id"],
                }
            else:
                links[href] = {"company_key": company_key, "job_id": None}
        return {
            "edition": manifest["edition"],
            "source_bundle_id": manifest["source_bundle_id"],
            "links": links,
        }


__all__ = ["SourceLibrary"]
