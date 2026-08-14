#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat


_SERVER_START = re.compile(r"(?m)^server\s*\{")
_ROOT_LOCATION = re.compile(r"^location\s+/\s*\{$")
_SHARED_AUTH = {
    "auth_basic": re.compile(r'^auth_basic\s+"Orbbec Agent Platform";$'),
    "auth_basic_user_file": re.compile(r"^auth_basic_user_file\s+\S+;$"),
    "auth_delay": re.compile(r"^auth_delay\s+1s;$"),
}

_FORMAL_ROOT = """\
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Connection "";
        proxy_set_header Authorization "";

        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 360s;
        proxy_send_timeout 360s;

        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Cache-Control "no-store" always;
        add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    }
"""


def _brace_delta(value: str) -> int:
    delta = 0
    quote: str | None = None
    escaped = False
    for character in value:
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote is not None:
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "#":
            break
        elif character == "{":
            delta += 1
        elif character == "}":
            delta -= 1
    return delta


def _matching_brace(value: str, opening: int) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening, len(value)):
        character = value[index]
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote is not None:
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "#":
            newline = value.find("\n", index)
            if newline < 0:
                break
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise ValueError("Nginx server block invalid")


def _https_agent_server(value: str) -> tuple[int, int, str]:
    selected = []
    for match in _SERVER_START.finditer(value):
        opening = value.find("{", match.start())
        end = _matching_brace(value, opening)
        block = value[match.start() : end]
        if (
            re.search(r"(?m)^\s*listen\s+443\s+ssl;\s*$", block)
            and re.search(
                r"(?m)^\s*server_name\s+agent\.orbbec\.com\.cn;\s*$", block
            )
        ):
            selected.append((match.start(), end, block))
    if len(selected) != 1:
        raise ValueError("exact Agent HTTPS server required")
    return selected[0]


def _transform_server(block: str) -> str:
    lines = block.splitlines(keepends=True)
    output: list[str] = []
    depth = 0
    removed_auth: set[str] = set()
    roots = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if depth == 1:
            if stripped == (
                "include /etc/nginx/snippets/"
                "orbbec-agent-demo-preview.conf;"
            ):
                index += 1
                continue
            matched_auth = next(
                (
                    name
                    for name, pattern in _SHARED_AUTH.items()
                    if pattern.fullmatch(stripped)
                ),
                None,
            )
            if matched_auth is not None:
                if matched_auth in removed_auth:
                    raise ValueError("shared authentication ambiguous")
                removed_auth.add(matched_auth)
                index += 1
                continue
            if _ROOT_LOCATION.fullmatch(stripped):
                roots += 1
                if roots > 1:
                    raise ValueError("root location ambiguous")
                location_depth = depth
                depth += _brace_delta(line)
                index += 1
                while index < len(lines) and depth > location_depth:
                    depth += _brace_delta(lines[index])
                    index += 1
                if depth != location_depth:
                    raise ValueError("root location invalid")
                output.append(_FORMAL_ROOT)
                continue
        output.append(line)
        depth += _brace_delta(line)
        if depth < 0:
            raise ValueError("Nginx server block invalid")
        index += 1
    if removed_auth != set(_SHARED_AUTH):
        raise ValueError("shared authentication boundary missing")
    if roots != 1:
        raise ValueError("root location boundary missing")
    if depth != 0:
        raise ValueError("Nginx server block invalid")
    return "".join(output)


def transform(value: str) -> str:
    start, end, block = _https_agent_server(value)
    return value[:start] + _transform_server(block) + value[end:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("destination")
    namespace = parser.parse_args()
    source = Path(namespace.source)
    destination = Path(namespace.destination)
    metadata = source.lstat()
    if (
        source.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or destination.exists()
        or destination.is_symlink()
    ):
        raise ValueError("Nginx transaction path invalid")
    rendered = transform(source.read_text(encoding="utf-8"))
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(rendered)
        stream.flush()
        os.fsync(stream.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
