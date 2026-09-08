"""Private file-backed S3 byte substitute shared by owned fixture processes."""

import hashlib
import io
from pathlib import Path

from app.attachments.validation import OpenedObject


class RecruitingObjects:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, key):
        return self.root / hashlib.sha256(key.encode()).hexdigest()

    def put_object(self, *, Bucket, Key, Body, ContentLength):
        assert Bucket == "owned-recruiting-files"
        with self._path(Key).open("xb") as output:
            while chunk := Body.read(65536):
                output.write(chunk)
        assert self._path(Key).stat().st_size == ContentLength

    def get_object(self, *, Bucket, Key, IfMatch):
        assert Bucket == "owned-recruiting-files"
        assert hashlib.sha256(self._path(Key).read_bytes()).hexdigest() == IfMatch
        return {"Body": self._path(Key).open("rb")}

    def delete_object(self, *, Bucket, Key):
        assert Bucket == "owned-recruiting-files"
        self._path(Key).unlink(missing_ok=True)

    def open(self, object_ref, immutable_locator=None):
        path = self._path(object_ref)
        locator = "etag:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert immutable_locator in (None, locator)
        return OpenedObject(io.BytesIO(path.read_bytes()), path.stat().st_size, locator)

    def delete(self, object_ref):
        self._path(object_ref).unlink(missing_ok=True)
