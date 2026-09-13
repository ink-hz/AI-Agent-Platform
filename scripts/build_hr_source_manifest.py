#!/usr/bin/env python3
"""Build immutable, server-side HR source content from an existing verified archive."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter
from collections.abc import Mapping
from html.parser import HTMLParser
from pathlib import Path

_BUNDLE_ID = "2ed2262c-63a0-47e5-996c-7d00eb0be1a0"
_BLOCKS = frozenset(
    {"p", "div", "li", "ul", "ol", "section", "article", "h1", "h2", "h3", "h4", "br"}
)
_SKIP = frozenset(
    {"script", "style", "nav", "form", "noscript", "footer", "svg", "canvas"}
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip = 0
        self._title = False

    def handle_starttag(self, tag, attrs):
        selected = tag.casefold()
        if selected in _SKIP:
            self._skip += 1
        if selected == "title":
            self._title = True
        if selected in _BLOCKS and not self._skip:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        selected = tag.casefold()
        if selected in _BLOCKS and not self._skip:
            self.parts.append("\n")
        if selected == "title":
            self._title = False
        if selected in _SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        if self._title:
            self.title_parts.append(data)
        self.parts.append(data)


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    raw = str(value).replace("\x00", " ")
    if re.search(r"<[a-z!/][^>]*>", raw, re.IGNORECASE):
        parser = _TextParser()
        parser.feed(raw)
        raw = "".join(parser.parts)
    lines = [
        " ".join(html.unescape(line).replace("\xa0", " ").split())
        for line in raw.splitlines()
    ]
    text = "\n".join(line for line in lines if line).strip()
    return None if not text or text in {"未公开", "无", "null", "None"} else text


def _split_description(value: object) -> tuple[str | None, str | None]:
    text = clean_text(value)
    if text is None:
        return None, None
    for marker in (
        "任职要求：",
        "任职要求:",
        "任职要求",
        "岗位要求：",
        "岗位要求:",
        "岗位要求",
    ):
        if marker in text:
            duty, requirement = text.split(marker, 1)
            duty = re.sub(r"^(?:一、)?(?:工作|岗位)?职责[：:]?\s*", "", duty).strip()
            requirement = re.sub(r"^(?:二、)?\s*", "", requirement).strip()
            return clean_text(duty), clean_text(requirement)
    return text, None


def _candidate_records(decoded: object) -> list[Mapping[str, object]]:
    if not isinstance(decoded, Mapping):
        return []
    for candidate in (
        decoded.get("jobs"),
        decoded.get("Data"),
        decoded.get("data", {}).get("job_post_list")
        if isinstance(decoded.get("data"), Mapping)
        else None,
    ):
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, Mapping)]
    for key in ("data", "items", "results", "positions", "career"):
        nested = decoded.get(key)
        selected = _candidate_records(nested)
        if selected:
            return selected
    return []


def _decode_evidence(body: bytes) -> tuple[object | None, str | None, str | None]:
    text = body.decode("utf-8-sig", errors="replace")
    try:
        return json.loads(text), None, None
    except json.JSONDecodeError:
        marker = "var data = JSON.parse(JSON.stringify("
        start = text.find(marker)
        if start >= 0:
            try:
                value, _ = json.JSONDecoder().raw_decode(
                    text[start + len(marker) :].lstrip()
                )
                return value, None, None
            except json.JSONDecodeError:
                pass
        parser = _TextParser()
        parser.feed(text)
        title = clean_text(" ".join(parser.title_parts))
        return None, title, clean_text("".join(parser.parts))


def _public_key(item: Mapping[str, object]) -> str | None:
    value = (
        item.get("identifier")
        or item.get("id")
        or item.get("jobId")
        or item.get("JobAdId")
        or item.get("Id")
        or item.get("publish_id")
    )
    if isinstance(value, Mapping):
        value = value.get("value") or value.get("name")
    return clean_text(value)


def _named(value: object) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("i18n_name") or value.get("name") or value.get("value")
    if isinstance(value, list):
        parts = [_named(item) for item in value]
        return " / ".join(dict.fromkeys(item for item in parts if item)) or None
    return clean_text(value)


def _fields(item: Mapping[str, object]) -> list[dict[str, str]]:
    selected: list[tuple[str, str | None]] = []
    department = _named(item.get("department") or item.get("Org"))
    education = _named(
        item.get("education") or item.get("Degree") or item.get("degree_require")
    )
    experience = _named(
        item.get("experience") or item.get("YearsOfWorking") or item.get("WorkTime")
    )
    salary = _named(item.get("Salary") or item.get("salary"))
    if salary is None and (
        item.get("minSalary") is not None or item.get("maxSalary") is not None
    ):
        bounds = "–".join(
            str(value)
            for value in (item.get("minSalary"), item.get("maxSalary"))
            if value is not None
        )
        unit = item.get("salaryUnit")
        salary = bounds + (f"（原始单位 {unit}）" if unit is not None else "")
    recruitment = item.get("recruit_type")
    info = item.get("job_post_info")
    if recruitment is None and isinstance(info, Mapping):
        recruitment = info.get("recruitment_type")
    category = item.get("job_category") or item.get("Category") or item.get("zhineng")
    commitment = item.get("commitment") or item.get("Kind")
    selected.extend(
        (
            ("部门", department),
            ("学历", education),
            ("经验", experience),
            ("薪资", salary),
            ("招聘类型", _named(recruitment) or _named(commitment)),
            ("岗位类别", _named(category)),
            ("相关专业", _named(item.get("about_major"))),
            ("招聘人数", _named(item.get("job_number"))),
        )
    )
    custom = item.get("customFields")
    if isinstance(custom, list):
        for field in custom:
            if isinstance(field, Mapping):
                selected.append(
                    (
                        _named(field.get("name")) or "公开字段",
                        _named(field.get("value")),
                    )
                )
    return [{"label": label, "value": value} for label, value in selected if value]


def _body(item: Mapping[str, object]) -> tuple[str | None, str | None]:
    explicit_duty = item.get("duty") or item.get("Duty") or item.get("responsibilities")
    explicit_requirement = (
        item.get("qualifications")
        or item.get("requirements")
        or item.get("requirement")
        or item.get("Require")
        or item.get("experienceRequirements")
        or item.get("job_require")
    )
    if explicit_duty is not None or explicit_requirement is not None:
        duty = clean_text(explicit_duty)
        requirement = clean_text(explicit_requirement)
        description = item.get("description") or item.get("job_descript")
        if duty is None and description is not None:
            duty, embedded_requirement = _split_description(description)
            requirement = requirement or embedded_requirement
        return duty, requirement
    return _split_description(item.get("description") or item.get("job_descript"))


def _channel_name(
    url: str, records: Mapping[str, Mapping[str, object]] | None = None
) -> str:
    lowered = url.casefold()
    if "internrecruitment" in lowered:
        return "实习招聘"
    if "campus-recruitment" in lowered or "campusrecruitment" in lowered or "/campus" in lowered:
        return "校园招聘"
    if "social-recruitment" in lowered or "socialrecruitment" in lowered or "socialeng" in lowered or "experienced" in lowered:
        return "社会招聘"
    if "special-recruitment" in lowered:
        return "专项招聘"
    source_types: Counter[str] = Counter()
    for item in (records or {}).values():
        recruitment = item.get("recruit_type")
        info = item.get("job_post_info")
        if recruitment is None and isinstance(info, Mapping):
            recruitment = info.get("recruitment_type")
        labels = []
        if isinstance(recruitment, Mapping):
            labels.extend((_named(recruitment), _named(recruitment.get("parent"))))
        labels.append(_named(item.get("Category")))
        source_labels = " ".join(value for value in labels if value)
        if "实习" in source_labels:
            source_types["实习招聘"] += 1
        elif "校" in source_labels:
            source_types["校园招聘"] += 1
        elif "社" in source_labels:
            source_types["社会招聘"] += 1
    if source_types:
        return source_types.most_common(1)[0][0]
    return "招聘官网"


def _source_status(item: Mapping[str, object] | None) -> str:
    value = item.get("status") if item is not None else None
    if isinstance(value, str) and value.casefold() in {"open", "closed", "unknown"}:
        return value.casefold()
    return "unknown"


def _document_state(channels: list[Mapping[str, object]]) -> str:
    states = {item.get("state") for item in channels}
    if states == {"succeeded"}:
        return "succeeded"
    if "succeeded" in states:
        return "partial"
    return "failed"


def _verify_required_input(bundle: Path, relative: str) -> bytes:
    checksums = {}
    for line in (bundle / "checksums.sha256").read_text("utf-8").splitlines():
        digest, name = line.split("  ", 1)
        checksums[name] = digest
    body = (bundle / relative).read_bytes()
    if checksums.get(relative) != hashlib.sha256(body).hexdigest():
        raise ValueError(f"source input checksum mismatch: {relative}")
    return body


def build_source_content(bundle: Path, output: Path) -> str:
    bundle = bundle.resolve()
    manifest = json.loads(_verify_required_input(bundle, "manifest.json"))
    if (
        manifest.get("bundle_id") != _BUNDLE_ID
        or manifest.get("job_count") != 3437
        or manifest.get("company_count") != 12
    ):
        raise ValueError("unexpected source bundle identity")
    catalog = json.loads(_verify_required_input(bundle, "source-catalog.json"))
    coverage = json.loads(_verify_required_input(bundle, "source-coverage.json"))
    jobs = [
        json.loads(line)
        for line in _verify_required_input(bundle, "normalized-jobs.jsonl")
        .decode()
        .splitlines()
        if line
    ]
    if len(jobs) != 3437 or len({job["job_id"] for job in jobs}) != 3437:
        raise ValueError("normalized job identity mismatch")
    evidence_index = json.loads(
        _verify_required_input(bundle, "raw-evidence-index.json")
    )
    evidence_by_sha = {item["sha256"]: item for item in evidence_index}
    decoded_by_sha: dict[
        str, tuple[dict[str, Mapping[str, object]], str | None, str | None]
    ] = {}
    for sha256, metadata in evidence_by_sha.items():
        path = bundle / metadata["locator"]
        body = path.read_bytes()
        if (
            hashlib.sha256(body).hexdigest() != sha256
            or len(body) != metadata["size_bytes"]
        ):
            raise ValueError("evidence identity mismatch")
        decoded, title, text = _decode_evidence(body)
        records = {
            _public_key(item): item
            for item in _candidate_records(decoded)
            if _public_key(item)
        }
        decoded_by_sha[sha256] = (records, title, text)

    catalog_by_key = {item["company_key"]: item for item in catalog["companies"]}
    coverage_by_key = {item["company_key"]: item for item in coverage["companies"]}
    jobs_by_company: dict[str, list[dict[str, object]]] = {
        key: [] for key in catalog_by_key
    }
    source_url_counts = Counter(job["source_url"] for job in jobs)
    fallback_counts = Counter()
    for job in jobs:
        sha256 = job["evidence_sha256"]
        raw = decoded_by_sha.get(sha256, ({}, None, None))[0].get(job["public_job_key"])
        if raw is None:
            duty = clean_text(job.get("duty_excerpt"))
            requirement = clean_text(job.get("requirement_excerpt"))
            fields: list[dict[str, str]] = []
            note = "归档岗位记录未能按公开岗位键确定匹配；职责与要求为规范化摘录，可能未保留原始段落。"
            fallback_counts[job["company_key"]] += 1
        else:
            duty, requirement = _body(raw)
            fields = _fields(raw)
            missing = []
            if duty is None:
                missing.append("岗位职责")
            if requirement is None:
                missing.append("任职要求")
            note = "正文已从确定归档岗位记录恢复并保留段落；字段仅来自公开原始记录。"
            if missing:
                note += " 未能从原始记录中单独确认" + "、".join(missing) + "字段。"
        status = _source_status(raw)
        if status == "unknown":
            note += " 原始记录未提供可确认的开放状态，状态标为 unknown。"
        company_coverage = coverage_by_key[job["company_key"]]
        channel_entry = next(
            (
                item
                for item in company_coverage["channels"]
                if item.get("evidence_sha256") == sha256
            ),
            None,
        )
        channel = _channel_name(
            channel_entry["source_url"] if channel_entry else job["source_url"],
            decoded_by_sha.get(sha256, ({}, None, None))[0],
        )
        direct = source_url_counts[job["source_url"]] == 1 and (
            job["public_job_key"] in job["source_url"]
            or "detail" in job["source_url"].casefold()
        )
        jobs_by_company[job["company_key"]].append(
            {
                "job_id": job["job_id"],
                "title": job["title"],
                "location": job["location"],
                "status": status,
                "source_url": job["source_url"],
                "observed_at": job["observed_at"],
                "channel": channel,
                "duty": duty,
                "requirement": requirement,
                "fields": fields,
                "evidence_sha256": sha256,
                "public_job_key": job["public_job_key"],
                "source_kind": "job" if direct else "archive",
                "content_note": note,
            }
        )

    output.mkdir(parents=True, exist_ok=True)
    manifest_companies = []
    for company_key, company_catalog in catalog_by_key.items():
        company_coverage = coverage_by_key[company_key]
        channels = [
            {
                "channel": _channel_name(
                    item["source_url"],
                    decoded_by_sha.get(item.get("evidence_sha256"), ({}, None, None))[
                        0
                    ],
                ),
                "source_url": item["source_url"],
                "state": item["state"],
                "observed_at": item["observed_at"],
                "job_count": item.get("job_count"),
                "error_code": item.get("error_code"),
            }
            for item in company_coverage["channels"]
        ]
        channels.extend(
            {
                "channel": "公司官网资料",
                "source_url": item["source_url"],
                "state": item["state"],
                "observed_at": item["observed_at"],
                "job_count": None,
                "error_code": item.get("error_code"),
            }
            for item in company_coverage["document_channels"]
        )
        documents = []
        for item in company_coverage["document_channels"]:
            sha256 = item.get("evidence_sha256")
            if item.get("state") != "succeeded" or not sha256:
                continue
            _, title, text = decoded_by_sha[sha256]
            if text:
                documents.append(
                    {
                        "title": title or "公司官网资料",
                        "text": text,
                        "source_url": item["source_url"],
                        "observed_at": item["observed_at"],
                        "evidence_sha256": sha256,
                    }
                )
        company_jobs = sorted(
            jobs_by_company[company_key],
            key=lambda item: (item["title"].casefold(), item["job_id"]),
        )
        limitations = list(
            dict.fromkeys(
                company_coverage.get("limitations", [])
                + company_coverage.get("document_limitations", [])
            )
        )
        if fallback_counts[company_key]:
            limitations.append(
                f"{fallback_counts[company_key]} 个岗位未能按公开岗位键确定匹配，详情明确标注为规范化摘录。"
            )
        company = {
            "company_key": company_key,
            "name": company_catalog["canonical_name"],
            "aliases": company_catalog["aliases"],
            "job_count": len(company_jobs),
            "coverage_state": company_coverage["state"],
            "document_state": _document_state(company_coverage["document_channels"]),
            "observed_at": company_coverage["observed_at"],
        }
        content = {
            "company": company,
            "documents": documents,
            "channels": channels,
            "limitations": limitations,
            "jobs": company_jobs,
        }
        body = _canonical(content) + b"\n"
        path = output / f"{company_key}.json"
        path.write_bytes(body)
        manifest_companies.append(
            {
                **company,
                "path": path.name,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
            }
        )
    publication = {
        "schema_version": 1,
        "source_bundle_id": _BUNDLE_ID,
        "observed_at": max(item["observed_at"] for item in evidence_index),
        "job_count": len(jobs),
        "companies": manifest_companies,
    }
    edition = "source-" + hashlib.sha256(_canonical(publication)).hexdigest()[:20]
    publication["edition"] = edition
    (output / "manifest.json").write_bytes(_canonical(publication) + b"\n")
    return edition


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build_source_content(args.bundle, args.output))


if __name__ == "__main__":
    main()
