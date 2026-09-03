"""Build the small, deterministic attachment security fixture corpus."""

from __future__ import annotations

import binascii
import io
import struct
import zipfile
import zlib
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

ROOT = Path(__file__).resolve().parent
_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def _zip_entry(archive: zipfile.ZipFile, name: str, data: str | bytes) -> None:
    info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o600 << 16
    archive.writestr(info, data)


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", binascii.crc32(body))
        )

    pixels = b"\x00\xff\x00\x00\xff"
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def _pdf(catalog_extra: bytes = b"") -> bytes:
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b" >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length 36 >>\nstream\nBT /F1 12 Tf 20 100 Td (Safe) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    output = bytearray(b"%PDF-1.4\n%\xff\xff\xff\xff\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f\n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n\n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def _compressed_active_pdf() -> bytes:
    output = bytearray(b"%PDF-1.5\n%\xff\xff\xff\xff\n")
    offsets = {}

    def emit(number: int, body: bytes) -> None:
        offsets[number] = len(output)
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")

    emit(1, b"<< /Type /Catalog /Pages 2 0 R /Open#41ction 5 0 R >>")
    emit(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    emit(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>")
    emit(4, b"<< /Length 0 >>\nstream\n\nendstream")
    object_data = b"5 0 << /S /Java#53cript /JS (compressed) >>"
    compressed = zlib.compress(object_data)
    emit(
        6,
        f"<< /Type /ObjStm /N 1 /First 4 /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode()
        + compressed
        + b"\nendstream",
    )
    xref_offset = len(output)
    entries = bytearray()
    for number in range(8):
        if number == 0:
            kind, first, second = 0, 0, 65535
        elif number == 5:
            kind, first, second = 2, 6, 0
        else:
            kind, first, second = 1, xref_offset if number == 7 else offsets[number], 0
        entries.extend(bytes((kind,)) + first.to_bytes(4, "big") + second.to_bytes(2, "big"))
    output.extend(
        f"7 0 obj\n<< /Type /XRef /Size 8 /Root 1 0 R /W [1 4 2] /Length {len(entries)} >>\nstream\n".encode()
    )
    output.extend(entries)
    output.extend(b"\nendstream\nendobj\n")
    output.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(output)


_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "officeDocument"
)


def _office(
    path: Path,
    root_name: str,
    content_type: str,
    root_xml: str,
    *,
    extra_entries: tuple[tuple[str, str | bytes], ...] = (),
    package_relationships: str | None = None,
    content_types_xml: str | None = None,
) -> None:
    content_types = content_types_xml or (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/{root_name}" ContentType="{content_type}"/>'
        "</Types>"
    )
    relationships = package_relationships or (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{_REL_NS}">'
        f'<Relationship Id="rId1" Type="{_OFFICE_REL}" Target="{root_name}"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        _zip_entry(archive, "[Content_Types].xml", content_types)
        _zip_entry(archive, "_rels/.rels", relationships)
        _zip_entry(archive, root_name, root_xml)
        for name, data in extra_entries:
            _zip_entry(archive, name, data)


def _docx(path: Path, **kwargs) -> None:
    _office(
        path,
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
        **kwargs,
    )


def _mark_encrypted_zip(data: bytes) -> bytes:
    result = bytearray(data)
    offset = 0
    while True:
        offset = result.find(b"PK\x03\x04", offset)
        if offset < 0:
            break
        flags = struct.unpack_from("<H", result, offset + 6)[0]
        struct.pack_into("<H", result, offset + 6, flags | 1)
        offset += 4
    offset = 0
    while True:
        offset = result.find(b"PK\x01\x02", offset)
        if offset < 0:
            break
        flags = struct.unpack_from("<H", result, offset + 8)[0]
        struct.pack_into("<H", result, offset + 8, flags | 1)
        offset += 4
    return bytes(result)


def _mark_local_encrypted(data: bytes) -> bytes:
    result = bytearray(data)
    offset = 0
    while True:
        offset = result.find(b"PK\x03\x04", offset)
        if offset < 0:
            return bytes(result)
        flags = struct.unpack_from("<H", result, offset + 6)[0]
        struct.pack_into("<H", result, offset + 6, flags | 1)
        offset += 4


def main() -> None:
    png = _png()
    jpeg_output = io.BytesIO()
    Image.new("RGB", (1, 1), (0, 128, 255)).save(
        jpeg_output,
        format="JPEG",
        quality=85,
        optimize=False,
        progressive=False,
    )
    jpeg = jpeg_output.getvalue()
    (ROOT / "valid.png").write_bytes(png)
    (ROOT / "valid.jpg").write_bytes(jpeg)
    (ROOT / "polyglot.jpg").write_bytes(jpeg + b"<script>x</script>\xff\xd9")
    (ROOT / "concatenated.jpg").write_bytes(jpeg + jpeg)
    (ROOT / "valid.pdf").write_bytes(_pdf())
    (ROOT / "valid.txt").write_text("bounded plain text\n", encoding="utf-8")
    _docx(ROOT / "valid.docx")
    _office(
        ROOT / "valid.xlsx",
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>',
    )
    _office(
        ROOT / "valid.pptx",
        "ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        '<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
    )
    (ROOT / "extension_mismatch.pdf").write_bytes(png)
    (ROOT / "truncated.png").write_bytes(png[:-8])
    with zipfile.ZipFile(ROOT / "zip_bomb.docx", "w", zipfile.ZIP_DEFLATED) as archive:
        _zip_entry(archive, "[Content_Types].xml", b"A" * (2 * 1024 * 1024))
        _zip_entry(archive, "word/document.xml", b"B" * (2 * 1024 * 1024))
    encrypted = ROOT / "encrypted.docx"
    _docx(encrypted)
    encrypted.write_bytes(_mark_encrypted_zip(encrypted.read_bytes()))
    local_encrypted = ROOT / "local_encrypted.docx"
    _docx(local_encrypted)
    local_encrypted.write_bytes(_mark_local_encrypted(local_encrypted.read_bytes()))
    (ROOT / "encrypted_container.docx").write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
    )
    encrypted_pdf = PdfWriter()
    encrypted_page = encrypted_pdf.add_blank_page(width=200, height=200)
    binary_stream = DecodedStreamObject()
    binary_stream.set_data(b"\x00")
    encrypted_page[NameObject("/Contents")] = encrypted_pdf._add_object(binary_stream)
    encrypted_pdf.encrypt("secret")
    encrypted_output = io.BytesIO()
    encrypted_pdf.write(encrypted_output)
    encrypted_bytes = encrypted_output.getvalue().replace(
        b"%\xe2\xe3\xcf\xd3", b"%\x00\xe3\xcf\xd3", 1
    )
    (ROOT / "encrypted.pdf").write_bytes(encrypted_bytes)
    (ROOT / "active.svg").write_text(
        '<svg onload="alert(1)"></svg>\n', encoding="utf-8"
    )
    (ROOT / "active.html").write_text(
        "<!doctype html><script>alert(1)</script>\n", encoding="utf-8"
    )
    (ROOT / "bom_active.html").write_bytes(
        b"\xef\xbb\xbf  <!doctype html><script>alert(1)</script>\n"
    )
    (ROOT / "polyglot.png").write_bytes(png + b"<script>secret()</script>")

    escaped = _pdf(b"/Open#41ction << /S /Java#53cript /JS (x) >>")
    (ROOT / "escaped_active.pdf").write_bytes(escaped)
    external = _pdf(
        b"/Open#41ction << /S /U#52I /U#52I (https://example.invalid) >>"
    )
    (ROOT / "external_reference.pdf").write_bytes(external)
    (ROOT / "compressed_active.pdf").write_bytes(_compressed_active_pdf())
    (ROOT / "movie.pdf").write_bytes(
        _pdf(
            b"/Feature << /Subtype /Movie /Movie "
            b"<< /F (https://example.invalid) >> >>"
        )
    )

    external_rels = (
        '<?xml version="1.0"?>'
        f'<Relationships xmlns="{_REL_NS}">'
        f'<Relationship Id="rId1" Type="{_OFFICE_REL}" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid" TargetMode="External"/>'
        "</Relationships>"
    )
    _docx(ROOT / "external_relationship.docx", package_relationships=external_rels)
    application_rels = (
        '<?xml version="1.0"?>'
        f'<Relationships xmlns="{_REL_NS}">'
        '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="https://example.invalid/object" TargetMode="External"/>'
        "</Relationships>"
    )
    _docx(
        ROOT / "application_relationship.docx",
        extra_entries=(("word/_rels/document.xml.rels", application_rels),),
    )
    _docx(
        ROOT / "macro_content.docx",
        extra_entries=(("word/vbaProject.bin", b"macro"),),
    )
    _docx(
        ROOT / "activex.docx",
        extra_entries=(("word/activeX/activeX1.bin", b"control"),),
    )
    _docx(
        ROOT / "embedding.docx",
        extra_entries=(("word/embeddings/oleObject1.bin", b"ole"),),
    )
    _docx(ROOT / "traversal.docx", extra_entries=(("../outside.xml", b"x"),))
    _docx(ROOT / "untyped_part.docx", extra_entries=(("word/data.safe", b"x"),))
    connections_types = (
        '<?xml version="1.0"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/connections.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.connections+xml"/>'
        "</Types>"
    )
    connections_rels = (
        f'<Relationships xmlns="{_REL_NS}">'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/connections" Target="connections.xml"/>'
        "</Relationships>"
    )
    _office(
        ROOT / "external_connections.xlsx",
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
        content_types_xml=connections_types,
        extra_entries=(
            (
                "xl/connections.xml",
                '<connections xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><connection><dbPr connection="Data Source=external.invalid"/></connection></connections>',
            ),
            ("xl/_rels/workbook.xml.rels", connections_rels),
        ),
    )
    (ROOT / "polyglot.docx").write_bytes(
        (ROOT / "valid.docx").read_bytes() + b"<script>secret()</script>"
    )
    _docx(
        ROOT / "malformed_relationship.docx",
        package_relationships="<Relationships><Relationship",
    )

    _docx(ROOT / "corrupt_crc.docx")
    corrupt = bytearray((ROOT / "corrupt_crc.docx").read_bytes())
    central = corrupt.find(b"PK\x01\x02")
    while central >= 0:
        name_len = struct.unpack_from("<H", corrupt, central + 28)[0]
        name = bytes(corrupt[central + 46 : central + 46 + name_len])
        if name == b"word/document.xml":
            crc = struct.unpack_from("<I", corrupt, central + 16)[0]
            struct.pack_into("<I", corrupt, central + 16, crc ^ 0x01010101)
            break
        central = corrupt.find(b"PK\x01\x02", central + 4)
    (ROOT / "corrupt_crc.docx").write_bytes(corrupt)

    _docx(ROOT / "forged_metadata.docx")
    forged = bytearray((ROOT / "forged_metadata.docx").read_bytes())
    central = forged.find(b"PK\x01\x02")
    while central >= 0:
        name_len = struct.unpack_from("<H", forged, central + 28)[0]
        name = bytes(forged[central + 46 : central + 46 + name_len])
        if name == b"word/document.xml":
            struct.pack_into("<I", forged, central + 24, 50 * 1024 * 1024)
            break
        central = forged.find(b"PK\x01\x02", central + 4)
    (ROOT / "forged_metadata.docx").write_bytes(forged)
    (ROOT / "script.sh").write_text("#!/bin/sh\nprintf unsafe\n", encoding="utf-8")
    (ROOT / "eicar.txt").write_text(
        "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
        encoding="ascii",
    )


if __name__ == "__main__":
    main()
