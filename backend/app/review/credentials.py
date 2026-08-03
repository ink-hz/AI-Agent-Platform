from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field


Runner = Callable[..., subprocess.CompletedProcess[str]]


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
    def __init__(self, *, runner: Runner = subprocess.run) -> None:
        self.runner = runner

    def resolve(self, reference: str) -> Credential:
        if reference.startswith("env:"):
            name = reference.removeprefix("env:")
            if not name or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in name):
                raise CredentialUnavailable("invalid environment credential reference")
            value = os.getenv(name, "").strip()
            if not value:
                raise CredentialUnavailable("environment credential unavailable")
            return Credential(value)
        if reference.startswith("keychain:"):
            value = reference.removeprefix("keychain:")
            service, separator, account = value.rpartition("/")
            if not separator or not service or not account:
                raise CredentialUnavailable("invalid keychain credential reference")
            result = self.runner(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-a",
                    account,
                    "-s",
                    service,
                    "-w",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            token = result.stdout.strip() if result.returncode == 0 else ""
            if not token:
                raise CredentialUnavailable("keychain credential unavailable")
            return Credential(token)
        raise CredentialUnavailable("unsupported credential reference")
