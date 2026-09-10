"""Durable private document extraction; parser children only see bounded bytes."""

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from psycopg.types.json import Jsonb

from .types import MaterialText, content_sha256, problem

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RELEASES = {"application/pdf": "pdf-pypdf-v1", DOCX: "docx-xml-v1"}
MAX_SOURCE = 20 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_TEXT = 1000000


def _failure(code):
    return {
        "state": "failed",
        "error_code": code,
        "text": "",
        "coverage_complete": False,
        "coverage_notes": [code],
    }


def _extract(data, mime, expanded):
    notes = []
    parts = []

    def add(text):
        parts.append(text)
        if sum(map(len, parts)) > MAX_TEXT:
            raise OverflowError()

    if mime == "application/pdf":
        from pypdf import PdfReader, filters

        for limit in (
            "ZLIB_MAX_OUTPUT_LENGTH",
            "LZW_MAX_OUTPUT_LENGTH",
            "RUN_LENGTH_MAX_OUTPUT_LENGTH",
            "JBIG2_MAX_OUTPUT_LENGTH",
        ):
            setattr(filters, limit, expanded)

        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            return _failure("protected_document")
        if len(reader.pages) > 500:
            return _failure("page_limit")
        for number, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content is not None and len(content.get_data()) > expanded:
                raise OverflowError()
            text = page.extract_text() or ""
            add(text)
            if not text.strip():
                notes.append(
                    f"Page {number}: no extractable text; OCR was not performed."
                )
            if page.get("/Resources", {}).get("/XObject") or page.get("/Annots"):
                notes.append(
                    f"Page {number}: visual objects or annotations are not covered."
                )
        # PDF text extraction cannot establish visual/reading-order completeness.
        notes.append(
            "Extracted PDF text only; visual fidelity and reading order are not verified."
        )
    elif mime == DOCX:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2048 or sum(x.file_size for x in entries) > expanded:
                raise OverflowError()
            if any(x.flag_bits & 1 for x in entries):
                return _failure("protected_document")
            names = [x.filename for x in entries]
            if len(names) != len(set(names)):
                return _failure("malformed_document")
            xml = archive.read("word/document.xml")
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                return _failure("malformed_document")
            root = ElementTree.fromstring(xml)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            for paragraph in root.iter(ns + "p"):
                text = "".join(
                    (el.text or "")
                    if el.tag == ns + "t"
                    else "\t"
                    if el.tag == ns + "tab"
                    else "\n"
                    if el.tag in (ns + "br", ns + "cr")
                    else ""
                    for el in paragraph.iter()
                )
                add(text)
            if any(
                n.startswith(
                    (
                        "word/media/",
                        "word/embeddings/",
                        "word/header",
                        "word/footer",
                        "word/footnotes",
                        "word/endnotes",
                    )
                )
                for n in names
            ):
                notes.append(
                    "Images, embedded objects, headers, footers, or notes are not included."
                )
            if any(
                el.tag.rsplit("}", 1)[-1]
                in ("drawing", "object", "pict", "altChunk", "del", "instrText")
                for el in root.iter()
            ):
                notes.append(
                    "Visual objects, alternate content, deleted text, or fields are not fully represented."
                )
            if not any(parts):
                notes.append("No extractable document text.")
    else:
        return dict(_failure("unsupported_kind"), state="unsupported")
    return {
        "state": "ready",
        "error_code": None,
        "text": "\n".join(parts),
        "coverage_complete": not notes,
        "coverage_notes": notes,
    }


def parse_bytes(data, mime, *, timeout_seconds=20, max_expanded_bytes=MAX_EXPANDED):
    if not isinstance(data, bytes) or len(data) > MAX_SOURCE:
        return _failure("size_limit")
    # Pipes carry plaintext only in memory. stderr is discarded to avoid parser diagnostics.
    try:
        child = subprocess.run(
            [sys.executable, "-m", __name__, mime, str(max_expanded_bytes)],
            cwd=Path(__file__).resolve().parents[2],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
        )
        if child.returncode:
            return _failure("parser_failed")
        return json.loads(child.stdout)
    except subprocess.TimeoutExpired:
        return _failure("parse_timeout")
    except (ValueError, OSError):
        return _failure("parser_failed")


def source_identity(row):
    return {
        name: str(row[name])
        for name in (
            "immutable_locator",
            "write_attempt_id",
            "detected_mime",
            "size_bytes",
        )
    }


class MaterialParsingService:
    def __init__(self, repository, materials, *, timeout_seconds=20):
        self.repo, self.materials = repository, materials
        self.timeout_seconds = timeout_seconds
        materials.parsing = self

    def request(self, owner_id, attachment_id, idempotency_key):
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise problem("invalid_input")
        row = self.materials._row(owner_id, attachment_id)
        if row["state"] != "ready":
            raise problem("reference_unavailable", http_status=410)
        release = RELEASES.get(row["detected_mime"])
        if not release:
            raise problem("unsupported_kind")
        self.materials._assert_current(owner_id, row)
        with self.repo.transaction() as c:
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (str(owner_id) + idempotency_key,),
            )
            c.execute(
                "SELECT * FROM platform_hr_agent.material_parse_requests WHERE owner_id=%s AND request_key=%s",
                (owner_id, idempotency_key),
            )
            previous = c.fetchone()
            if previous and previous["attachment_id"] != row["attachment_id"]:
                raise problem("idempotency_conflict", http_status=409)
            c.execute(
                "INSERT INTO platform_hr_agent.material_parses(parse_id,owner_id,attachment_id,source_sha256,parser_release,source_identity,state) VALUES(%s,%s,%s,%s,%s,%s,'queued') ON CONFLICT(owner_id,attachment_id,source_sha256,parser_release) DO NOTHING",
                (
                    uuid4(),
                    owner_id,
                    row["attachment_id"],
                    row["sha256"],
                    release,
                    Jsonb(source_identity(row)),
                ),
            )
            c.execute(
                "SELECT * FROM platform_hr_agent.material_parses WHERE owner_id=%s AND attachment_id=%s AND source_sha256=%s AND parser_release=%s",
                (owner_id, row["attachment_id"], row["sha256"], release),
            )
            task = c.fetchone()
            if previous and previous["parse_id"] != task["parse_id"]:
                raise problem("idempotency_conflict", http_status=409)
            if source_identity(row) != task["source_identity"]:
                raise problem("reference_unavailable", http_status=410)
            if previous:
                return previous["receipt"]
            receipt = {
                "parse_id": str(task["parse_id"]),
                "attachment_id": str(row["attachment_id"]),
                "state": task["state"],
                "parser_release": release,
            }
            c.execute(
                "INSERT INTO platform_hr_agent.material_parse_requests VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    owner_id,
                    idempotency_key,
                    row["attachment_id"],
                    task["parse_id"],
                    Jsonb(receipt),
                ),
            )
        return receipt

    def process_one(self, worker_id, lease_seconds=60):
        with self.repo.transaction() as c:
            c.execute(
                "SELECT * FROM platform_hr_agent.material_parses WHERE state='queued' OR (state='processing' AND lease_until<now()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )
            task = c.fetchone()
            if not task:
                return False
            c.execute(
                "UPDATE platform_hr_agent.material_parses SET state='processing',attempts=attempts+1,lease_owner=%s,lease_until=now()+(%s * interval '1 second') WHERE parse_id=%s RETURNING attempts",
                (worker_id, max(1, lease_seconds), task["parse_id"]),
            )
            attempt = c.fetchone()["attempts"]
        try:
            row = self.materials._row(task["owner_id"], task["attachment_id"])
            if (
                row["state"] != "ready"
                or bytes(row["sha256"]) != bytes(task["source_sha256"])
                or source_identity(row) != task["source_identity"]
            ):
                raise ValueError()
            data = self.materials._read_bytes(row)
            result = (
                parse_bytes(
                    data, row["detected_mime"], timeout_seconds=self.timeout_seconds
                )
                if attempt <= 3
                else _failure("attempt_limit")
            )
            self.materials._assert_current(task["owner_id"], row)
        except Exception:  # noqa: BLE001 - source/storage failures must stay opaque
            result = _failure("source_unavailable")
        sealed = self.repo._seal(
            "material_parses", task["parse_id"], "sealed_content", result
        )
        with self.repo.transaction() as c:
            c.execute(
                "UPDATE platform_hr_agent.material_parses SET state=%s,sealed_content=%s,sealed_content_key_version=%s,error_code=%s,lease_owner=NULL,lease_until=NULL WHERE parse_id=%s AND state='processing' AND lease_owner=%s AND attempts=%s AND lease_until>now()",
                (
                    result["state"],
                    sealed["sealed_content"],
                    sealed["sealed_content_key_version"],
                    result["error_code"],
                    task["parse_id"],
                    worker_id,
                    attempt,
                ),
            )
        return True

    def existing(self, owner_id, row, view):
        release = RELEASES.get(row["detected_mime"])
        if not release:
            view["parse_state"] = "unsupported"
            return view, None
        with self.repo.transaction() as c:
            c.execute(
                "SELECT * FROM platform_hr_agent.material_parses WHERE owner_id=%s AND attachment_id=%s AND source_sha256=%s AND parser_release=%s",
                (owner_id, row["attachment_id"], row["sha256"], release),
            )
            task = c.fetchone()
        if not task:
            return view, None
        if source_identity(row) != task["source_identity"]:
            raise problem("reference_unavailable", http_status=410)
        view.update(
            parse_state=task["state"],
            parser_release=release,
            parse_id=str(task["parse_id"]),
            error_code=task["error_code"],
        )
        if task["state"] != "ready":
            return view, None
        result = self.repo._unseal(
            "material_parses", task["parse_id"], "sealed_content", task
        )
        self.materials._assert_current(owner_id, row)
        payload = {
            "original_ref": view["original_ref"],
            "parser_release": release,
            "text": result["text"],
            "coverage_complete": result["coverage_complete"],
            "coverage_notes": result["coverage_notes"],
        }
        ref = {
            "kind": "material",
            "id": f"{row['attachment_id']}:text",
            "revision": bytes(row["sha256"]).hex() + ":" + release,
            "sha256": content_sha256(payload),
        }
        view.update(
            text_ref=ref,
            coverage_complete=result["coverage_complete"],
            coverage_notes=result["coverage_notes"],
        )
        return view, MaterialText(
            ref,
            view["original_ref"],
            release,
            result["text"],
            result["coverage_complete"],
            coverage_notes=tuple(result["coverage_notes"]),
        )


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        result = _extract(
            sys.stdin.buffer.read(MAX_SOURCE + 1), sys.argv[1], int(sys.argv[2])
        )
    except (OverflowError, MemoryError):
        result = _failure("size_limit")
    except Exception as error:  # noqa: BLE001 - parser diagnostics may contain source text
        result = _failure(
            "size_limit"
            if type(error).__name__ == "LimitReachedError"
            else "malformed_document"
        )
    sys.stdout.write(json.dumps(result))
