from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from botocore.config import Config as BotoConfig

from app.attachments.object_writer import AttachmentObjectWriter


def test_seekable_staged_body_survives_real_botocore_checksum_and_retry():
    payload = b"payload-block-" * 100_000
    received = []
    headers = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_PUT(self):  # noqa: N802 - stdlib HTTP handler contract
            length = int(self.headers["Content-Length"])
            received.append(self.rfile.read(length))
            headers.append(dict(self.headers))
            if len(received) == 1:
                body = b"<Error><Code>InternalError</Code></Error>"
                self.send_response(500)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("ETag", '"fixture-etag"')
            self.send_header("x-amz-version-id", "fixture-version")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        client = boto3.client(
            "s3",
            endpoint_url=f"http://127.0.0.1:{server.server_port}",
            region_name="us-east-1",
            aws_access_key_id="fixture-access",
            aws_secret_access_key="fixture-secret",
            config=BotoConfig(
                s3={"addressing_style": "path"},
                retries={"mode": "standard", "max_attempts": 2},
            ),
        )
        receipt = AttachmentObjectWriter(client, "fixture-bucket").put_stream(
            "fixture-object", GeneratedNonSeekable(payload), len(payload)
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert receipt.size_bytes == len(payload)
    assert receipt.sha256 == hashlib.sha256(payload).digest()
    assert received == [payload, payload]
    assert all(item["If-None-Match"] == "*" for item in headers)
    assert all("x-amz-checksum-crc32" in item for item in headers)


class GeneratedNonSeekable:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.largest_read = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload) - self._offset
        self.largest_read = max(self.largest_read, size)
        result = self._payload[self._offset : self._offset + size]
        self._offset += len(result)
        return result
