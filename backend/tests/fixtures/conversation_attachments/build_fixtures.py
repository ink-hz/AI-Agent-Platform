"""Build the small, deterministic attachment security fixture corpus."""

from __future__ import annotations

import binascii
import io
import struct
import zipfile
import zlib
from pathlib import Path

from PIL import Image

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


def _pdf() -> bytes:
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
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


def _office(path: Path, root_name: str, content_type: str) -> None:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Override PartName="/{root_name}" ContentType="{content_type}"/>'
        "</Types>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        _zip_entry(archive, "[Content_Types].xml", content_types)
        _zip_entry(archive, root_name, '<?xml version="1.0"?><root/>')


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
    (ROOT / "valid.pdf").write_bytes(_pdf())
    (ROOT / "valid.txt").write_text("bounded plain text\n", encoding="utf-8")
    _office(
        ROOT / "valid.docx",
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )
    _office(
        ROOT / "valid.xlsx",
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    )
    _office(
        ROOT / "valid.pptx",
        "ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    )
    (ROOT / "extension_mismatch.pdf").write_bytes(png)
    (ROOT / "truncated.png").write_bytes(png[:-8])
    with zipfile.ZipFile(ROOT / "zip_bomb.docx", "w", zipfile.ZIP_DEFLATED) as archive:
        _zip_entry(archive, "[Content_Types].xml", b"A" * (2 * 1024 * 1024))
        _zip_entry(archive, "word/document.xml", b"B" * (2 * 1024 * 1024))
    encrypted = ROOT / "encrypted.docx"
    _office(
        encrypted,
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )
    encrypted.write_bytes(_mark_encrypted_zip(encrypted.read_bytes()))
    (ROOT / "encrypted.pdf").write_bytes(
        b"%PDF-1.4\n1 0 obj << /Encrypt 2 0 R >> endobj\n%%EOF\n"
    )
    (ROOT / "active.svg").write_text(
        '<svg onload="alert(1)"></svg>\n', encoding="utf-8"
    )
    (ROOT / "active.html").write_text(
        "<!doctype html><script>alert(1)</script>\n", encoding="utf-8"
    )
    (ROOT / "script.sh").write_text("#!/bin/sh\nprintf unsafe\n", encoding="utf-8")
    (ROOT / "eicar.txt").write_text(
        "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
        encoding="ascii",
    )


if __name__ == "__main__":
    main()
