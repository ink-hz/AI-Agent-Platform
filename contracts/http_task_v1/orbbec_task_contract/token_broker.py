from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import ValidationError

from .models import TokenBrokerRequest, TokenBrokerResponse


class TokenBrokerError(RuntimeError):
    """A safe error raised without exposing broker output or task tokens."""


class TaskTokenBroker:
    def __init__(self, executable: Path, *, timeout_seconds: float = 5.0) -> None:
        if not executable.is_absolute():
            raise ValueError("token broker executable path must be absolute")
        if timeout_seconds <= 0:
            raise ValueError("token broker timeout must be positive")
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def issue(self, request: TokenBrokerRequest) -> str:
        try:
            completed = subprocess.run(
                [str(self._executable)],
                input=request.model_dump_json() + "\n",
                text=True,
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TokenBrokerError("token broker timed out") from exc
        except OSError as exc:
            raise TokenBrokerError("token broker could not be executed") from exc
        if completed.returncode != 0:
            raise TokenBrokerError(
                f"token broker exited with status {completed.returncode}"
            )
        lines = completed.stdout.splitlines()
        if len(lines) != 1:
            raise TokenBrokerError("token broker returned an invalid response")
        try:
            response = TokenBrokerResponse.model_validate_json(lines[0])
        except (ValidationError, ValueError) as exc:
            raise TokenBrokerError("token broker returned an invalid response") from exc
        return response.token
