from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path


class BrainPromptIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BrainSystemPrompt:
    text: str
    sha256: str

    @classmethod
    def load(
        cls, path: Path, *, expected_sha256: str
    ) -> BrainSystemPrompt:
        try:
            if (
                not isinstance(path, Path)
                or not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in expected_sha256
                )
            ):
                raise ValueError
            raw = path.read_bytes()
            if (
                not raw
                or raw.startswith(b"\xef\xbb\xbf")
                or b"\r" in raw
                or not raw.endswith(b"\n")
                or raw.endswith(b"\n\n")
            ):
                raise ValueError
            text = raw.decode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            if not hmac.compare_digest(digest, expected_sha256):
                raise BrainPromptIntegrityError("Brain prompt sha256 mismatch")
            return cls(text=text, sha256=digest)
        except BrainPromptIntegrityError:
            raise
        except (OSError, UnicodeError, ValueError):
            raise BrainPromptIntegrityError("Brain prompt artifact invalid") from None
