from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.local_secrets import SecretFileUnavailable, read_secret_file


class CredentialUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Credential:
    _token: str = field(repr=False)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def __repr__(self) -> str:
        return "Credential(_token=<redacted>)"


class CredentialResolver:
    def resolve(self, reference: str) -> Credential:
        if reference.startswith("env:"):
            name = reference.removeprefix("env:")
            if not name or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in name):
                raise CredentialUnavailable("invalid environment credential reference")
            value = os.getenv(name, "").strip()
            if not value:
                raise CredentialUnavailable("environment credential unavailable")
            return Credential(value)
        if reference.startswith("file:"):
            path = reference.removeprefix("file:")
            if not path:
                raise CredentialUnavailable("invalid file credential reference")
            try:
                return Credential(read_secret_file(path))
            except SecretFileUnavailable as error:
                raise CredentialUnavailable("file credential unavailable") from error
        raise CredentialUnavailable("unsupported credential reference")
