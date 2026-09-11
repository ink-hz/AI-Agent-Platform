import io
import zipfile
from uuid import uuid4

import pytest
from app.hr_agent import material_parsing as parsing
from app.hr_agent.types import HrAgentProblem
from tests.test_hr_agent_materials import uploaded as uploaded  # noqa: PLC0414
from tests.test_hr_agent_routes import database as database  # noqa: PLC0414
from tests.test_hr_agent_routes import secured as secured  # noqa: PLC0414

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def docx(extra=b""):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        z.writestr(
            "word/document.xml",
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>First paragraph</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Last table cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>',
        )
        if extra:
            z.writestr("word/media/image.bin", extra)
    return out.getvalue()


def pdf(blank=False, protected=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in ("First page", "Last page"):
        page = writer.add_blank_page(width=300, height=300)
        if not blank:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    if protected:
        writer.encrypt("secret")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_bytes_order_coverage_and_failures():
    for data, mime, last in [
        (pdf(), "application/pdf", "Last page"),
        (docx(), DOCX, "Last table cell"),
    ]:
        result = parsing.parse_bytes(data, mime)
        assert result["state"] == "ready"
        assert last in result["text"]
    for data, mime in [(pdf(blank=True), "application/pdf"), (docx(b"image"), DOCX)]:
        result = parsing.parse_bytes(data, mime)
        assert not result["coverage_complete"]
        assert result["coverage_notes"]
    assert (
        parsing.parse_bytes(b"broken", "application/pdf")["error_code"]
        == "malformed_document"
    )
    assert (
        parsing.parse_bytes(pdf(protected=True), "application/pdf")["error_code"]
        == "protected_document"
    )
    assert (
        parsing.parse_bytes(docx(b"x" * 100000), DOCX, max_expanded_bytes=10000)[
            "error_code"
        ]
        == "size_limit"
    )
    assert (
        parsing.parse_bytes(pdf(), "application/pdf", timeout_seconds=0.000001)[
            "error_code"
        ]
        == "parse_timeout"
    )


def test_docx_reports_omml_math_and_word_symbols_as_partial_coverage():
    data = docx()
    source = io.BytesIO(data)
    output = io.BytesIO()
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(output, "w") as rewritten:
        for entry in archive.infolist():
            body = archive.read(entry)
            if entry.filename == "word/document.xml":
                body = body.replace(
                    b"</w:body>",
                    b"<w:p><w:r><w:t>Required formula: </w:t></w:r>"
                    b'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>x=7</m:t></m:r></m:oMath>'
                    b'<w:r><w:sym w:font="Symbol" w:char="F061"/></w:r></w:p></w:body>',
                )
            rewritten.writestr(entry, body)

    result = parsing.parse_bytes(output.getvalue(), DOCX)

    assert result["state"] == "ready"
    assert "Required formula: " in result["text"]
    assert result["coverage_complete"] is False
    assert any(
        "equations or symbols" in note.lower() for note in result["coverage_notes"]
    )


def test_durable_queue_reclaim_and_source_scope(uploaded, database):
    _, _, repo, owner, materials, aid, _, store = uploaded
    import hashlib

    data = docx()
    store.objects[next(iter(store.objects))] = data
    with database.admin_connection() as conn:
        conn.execute(
            "UPDATE platform_attachments.attachments SET detected_mime=%s,size_bytes=%s,sha256=%s WHERE attachment_id=%s",
            (DOCX, len(data), hashlib.sha256(data).digest(), aid),
        )
    service = parsing.MaterialParsingService(repo, materials)
    assert materials.resolve(owner, aid)["text_ref"] is None
    key = str(uuid4())
    receipt = service.request(owner, aid, key)
    assert service.request(owner, aid, key) == receipt
    assert service.request(owner, aid, str(uuid4()))["parse_id"] == receipt["parse_id"]
    with pytest.raises(HrAgentProblem):
        service.request(uuid4(), aid, str(uuid4()))
    with database.admin_connection() as conn:
        conn.execute(
            "UPDATE platform_hr_agent.material_parses SET state='processing',lease_owner='dead',lease_until=now()-interval '1 second',attempts=1 WHERE parse_id=%s",
            (receipt["parse_id"],),
        )
    assert service.process_one("replacement")
    view = materials.resolve(owner, aid)
    assert view["parse_state"] == "ready"
    assert "Last table cell" in materials.read_text(owner, view["text_ref"]).text
    with database.admin_connection() as conn:
        row = conn.execute(
            "SELECT attempts,sealed_content FROM platform_hr_agent.material_parses WHERE parse_id=%s",
            (receipt["parse_id"],),
        ).fetchone()
        assert row[0] == 2 and b"Last table cell" not in bytes(row[1])
        conn.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
            (aid,),
        )
    with pytest.raises(HrAgentProblem):
        materials.read_text(owner, view["text_ref"])


def upload_document(uploaded, database, data, mime, name):
    import asyncio

    from app.attachments.derivatives import DerivativeBuilder
    from app.attachments.scanner import TrustedInternalScanner
    from app.attachments.validation import AttachmentValidator
    from app.attachments.worker import AttachmentProcessor
    from app.attachments.worker_runtime import AttachmentProcessingRepository

    client, headers, repo, _owner, _materials, _, _, store = uploaded
    response = client.post(
        "/api/v1/attachments/uploads",
        json={
            "conversation_id": None,
            "original_name": name,
            "declared_mime": mime,
            "declared_size": len(data),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    receipt = response.json()
    uid = receipt["upload_id"]
    assert (
        client.put(
            f"/api/v1/attachments/uploads/{uid}/content",
            content=data,
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(data)),
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/attachments/uploads/{uid}/complete", headers=headers
        ).status_code
        == 200
    )
    processor = AttachmentProcessor(
        repository=AttachmentProcessingRepository(
            database.dsn.replace(
                "user=platform_control_app", "user=platform_brain_worker"
            ),
            content_codec=repo.codec,
        ),
        object_store=store,
        validator=AttachmentValidator(),
        scanner=TrustedInternalScanner(),
        derivatives=DerivativeBuilder(),
        worker_id="parse-upload",
    )
    for _ in range(4):
        if not asyncio.run(processor.process_next()):
            break
    return receipt["attachment_id"]


@pytest.mark.parametrize("kind", ["pdf", "docx"])
def test_real_http_binary_upload_parse_and_read(uploaded, database, kind):
    client, headers, repo, owner, materials, _, _, _ = uploaded
    service = parsing.MaterialParsingService(repo, materials)
    data, mime, last = (
        (pdf(), "application/pdf", "Last page")
        if kind == "pdf"
        else (docx(), DOCX, "Last table cell")
    )
    aid = upload_document(uploaded, database, data, mime, "public." + kind)
    response = client.get("/api/hr/agent/materials/" + aid)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "ready", response.json()
    assert response.json()["text_ref"] is None
    endpoint = "/api/hr/agent/materials/" + aid + "/parse"
    assert client.post(endpoint, json={}).status_code == 403
    receipt = client.post(endpoint, json={}, headers=headers)
    assert receipt.status_code == 202, receipt.text
    assert client.post(endpoint, json={}, headers=headers).json() == receipt.json()
    assert service.process_one("binary-parser")
    view = client.get("/api/hr/agent/materials/" + aid).json()
    assert view["parse_state"] == "ready", view
    assert last in materials.read_text(owner, view["text_ref"]).text
    assert not service.process_one("binary-parser")
    assert client.post(endpoint, json={}, headers=headers).json() == receipt.json()


def test_real_process_death_reclaimed_without_plaintext(uploaded, database, tmp_path):
    import multiprocessing
    import time

    _client, _headers, repo, owner, materials, _, _, store = uploaded
    service = parsing.MaterialParsingService(repo, materials)
    aid = upload_document(uploaded, database, pdf(), "application/pdf", "public.pdf")
    receipt = service.request(owner, aid, str(uuid4()))
    original = store.read_verified
    marker = tmp_path / "reading"

    def blocked(asset):
        marker.touch()
        time.sleep(60)

    store.read_verified = blocked
    process = multiprocessing.get_context("fork").Process(
        target=service.process_one, args=("killed-parser", 1)
    )
    process.start()
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        process.kill()
        process.join(5)
        assert process.exitcode < 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        store.read_verified = original
    with database.admin_connection() as conn:
        row = conn.execute(
            "SELECT state,attempts,sealed_content FROM platform_hr_agent.material_parses WHERE parse_id=%s",
            (receipt["parse_id"],),
        ).fetchone()
        assert row == ("processing", 1, None)
        conn.execute(
            "UPDATE platform_hr_agent.material_parses SET lease_until=now()-interval '1 second' WHERE parse_id=%s",
            (receipt["parse_id"],),
        )
    assert service.process_one("new-process")
    assert materials.resolve(owner, aid)["parse_state"] == "ready"
    assert list(repo.settings.work_dir.rglob("*")) == []


def test_pdf_decompression_bound():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    stream = DecodedStreamObject()
    stream.set_data(b" " * 100000)
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    assert (
        parsing.parse_bytes(
            output.getvalue(), "application/pdf", max_expanded_bytes=10000
        )["error_code"]
        == "size_limit"
    )


def test_parse_key_conflict_and_revoked_during_processing(uploaded, database):
    _client, _headers, repo, owner, materials, _, _, store = uploaded
    service = parsing.MaterialParsingService(repo, materials)
    first = upload_document(uploaded, database, pdf(), "application/pdf", "first.pdf")
    second = upload_document(uploaded, database, pdf(), "application/pdf", "second.pdf")
    key = str(uuid4())
    service.request(owner, first, key)
    with pytest.raises(HrAgentProblem) as error:
        service.request(owner, second, key)
    assert error.value.http_status == 409
    old = store.read_verified

    def revoked(asset):
        data = old(asset)
        with database.admin_connection() as conn:
            conn.execute(
                "UPDATE platform_attachments.attachments SET state='quarantined' WHERE attachment_id=%s",
                (first,),
            )
        return data

    store.read_verified = revoked
    assert service.process_one("revoked-parser")
    with database.admin_connection() as conn:
        row = conn.execute(
            "SELECT state,error_code,sealed_content FROM platform_hr_agent.material_parses WHERE attachment_id=%s",
            (first,),
        ).fetchone()
        assert row[0:2] == ("failed", "source_unavailable")
        assert b"Last page" not in bytes(row[2])
    assert materials.resolve(owner, first)["text_ref"] is None


def test_parse_uuid_case_retry_has_one_request_and_preserves_conflict(
    uploaded, database
):
    _client, _headers, repo, owner, materials, _, _, _store = uploaded
    service = parsing.MaterialParsingService(repo, materials)
    first = upload_document(uploaded, database, pdf(), "application/pdf", "first.pdf")
    second = upload_document(uploaded, database, pdf(), "application/pdf", "second.pdf")
    key = str(uuid4())
    service.request(owner, first, key.upper())
    service.request(owner, first, key.lower())
    with repo.transaction() as c:
        c.execute(
            "SELECT count(*) AS n FROM platform_hr_agent.material_parse_requests WHERE owner_id=%s",
            (owner,),
        )
        assert c.fetchone()["n"] == 1
    with pytest.raises(HrAgentProblem) as error:
        service.request(owner, second, key.lower())
    assert error.value.problem["code"] == "idempotency_conflict"
    assert service.process_one("uuid-case-retry")
    assert not service.process_one("uuid-case-retry-again")


def test_legacy_uppercase_uuid_parse_receipt_is_reused(uploaded, database):
    _client, _headers, repo, owner, materials, _, _, _store = uploaded
    service = parsing.MaterialParsingService(repo, materials)
    first = upload_document(uploaded, database, pdf(), "application/pdf", "first.pdf")
    second = upload_document(uploaded, database, pdf(), "application/pdf", "second.pdf")
    key = str(uuid4())
    receipt = service.request(owner, first, key)
    # Reproduce the persisted spelling written by the previous release.
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_hr_agent.material_parse_requests SET request_key=%s WHERE owner_id=%s AND request_key=%s",
            (key.upper(), owner, key),
        )
    with pytest.raises(HrAgentProblem) as error:
        service.request(owner, second, key)
    assert error.value.problem["code"] == "idempotency_conflict"
    assert service.request(owner, first, key) == receipt
    with repo.transaction() as c:
        c.execute(
            "SELECT request_key FROM platform_hr_agent.material_parse_requests WHERE owner_id=%s",
            (owner,),
        )
        assert [r["request_key"] for r in c.fetchall()] == [key.upper()]
    assert service.process_one("legacy-uuid-retry")


def test_noncanonical_opaque_parse_keys_remain_case_sensitive(uploaded, database):
    _client, _headers, repo, owner, materials, _, _, _store = uploaded
    service = parsing.MaterialParsingService(repo, materials)
    first = upload_document(uploaded, database, pdf(), "application/pdf", "first.pdf")
    key = uuid4().hex
    service.request(owner, first, key.upper())
    service.request(owner, first, key.lower())
    with repo.transaction() as c:
        c.execute(
            "SELECT count(*) AS n FROM platform_hr_agent.material_parse_requests WHERE owner_id=%s",
            (owner,),
        )
        assert c.fetchone()["n"] == 2
    assert service.process_one("opaque-case-retry")
    assert not service.process_one("opaque-case-retry-again")


def test_worker_revalidates_owner_grant_before_source_io(
    uploaded, database, monkeypatch
):
    _client, _headers, repo, owner, materials, _, _, store = uploaded
    service = parsing.MaterialParsingService(repo, materials)
    aid = upload_document(uploaded, database, pdf(), "application/pdf", "grant.pdf")
    receipt = service.request(owner, aid, str(uuid4()))
    grant = {"allowed": False}
    calls = {"read": 0, "parse": 0}

    def authorize(owner_id, objects, refs, work_id):
        if not grant["allowed"]:
            raise HrAgentProblem("scope_denied", http_status=403)

    def unexpected_read(asset):
        calls["read"] += 1
        return b"private"

    def unexpected_parse(*args, **kwargs):
        calls["parse"] += 1
        return {"state": "ready", "text": "private"}

    repo.scope_validator = authorize
    store.read_verified = unexpected_read
    monkeypatch.setattr(parsing, "parse_bytes", unexpected_parse)

    assert service.process_one("revoked-owner")
    assert calls == {"read": 0, "parse": 0}
    with database.admin_connection() as conn:
        row = conn.execute(
            "SELECT state,error_code,lease_owner,lease_until FROM platform_hr_agent.material_parses WHERE parse_id=%s",
            (receipt["parse_id"],),
        ).fetchone()
    assert row == ("failed", "source_unavailable", None, None)


def test_worker_revalidates_owner_grant_before_ready_persistence(
    uploaded, database, monkeypatch
):
    _client, _headers, repo, owner, materials, _, _, _store = uploaded
    service = parsing.MaterialParsingService(repo, materials)
    aid = upload_document(uploaded, database, pdf(), "application/pdf", "mid-grant.pdf")
    receipt = service.request(owner, aid, str(uuid4()))
    grant = {"allowed": True}

    def authorize(owner_id, objects, refs, work_id):
        if not grant["allowed"]:
            raise HrAgentProblem("scope_denied", http_status=403)

    def revoke_during_parse(*args, **kwargs):
        grant["allowed"] = False
        return {
            "state": "ready",
            "error_code": None,
            "text": "private parsed text",
            "coverage_complete": True,
            "coverage_notes": [],
        }

    repo.scope_validator = authorize
    monkeypatch.setattr(parsing, "parse_bytes", revoke_during_parse)

    assert service.process_one("mid-revoked-owner")
    with database.admin_connection() as conn:
        row = conn.execute(
            "SELECT state,error_code,sealed_content,lease_owner,lease_until FROM platform_hr_agent.material_parses WHERE parse_id=%s",
            (receipt["parse_id"],),
        ).fetchone()
    assert row[0:2] == ("failed", "source_unavailable")
    assert b"private parsed text" not in bytes(row[2])
    assert row[3:] == (None, None)
