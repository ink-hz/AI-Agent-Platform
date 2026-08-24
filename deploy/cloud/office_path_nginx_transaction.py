#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat


_SERVER_START = re.compile(r"(?m)^server\s*\{")
_LOG_FORMAT = re.compile(
    r"(?ms)^\s*log_format\s+agent_platform_redacted\s+(?P<body>.*?);"
)
_EXPECTED_LOG_VARIABLES = (
    "$request_method",
    "$uri",
    "$status",
    "$body_bytes_sent",
    "$request_time",
)
_ZONES = (
    "limit_req_zone $binary_remote_addr zone=ai_admin_office_chat:10m rate=12r/m;",
    "limit_conn_zone $binary_remote_addr zone=ai_admin_office_conn:10m;",
)

_PLATFORM_ADMIN_LOCATIONS = """\
    location = /admin {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Authorization "";
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 330s;
        proxy_send_timeout 330s;
    }

    location ^~ /admin/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Authorization "";
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 330s;
        proxy_send_timeout 330s;
    }

"""

_OFFICE_LOCATIONS = """\
    location = /office {
        return 308 /office/$is_args$args;
    }

    location = /office/health {
        return 404;
    }

    location = /office/chat {
        limit_req zone=ai_admin_office_chat burst=6 nodelay;
        limit_conn ai_admin_office_conn 8;
        client_max_body_size 1m;
        proxy_pass http://127.0.0.1:8011;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Authorization "";
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 330s;
        proxy_send_timeout 330s;
        add_header Cache-Control "private, no-store" always;
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    }

    location = /office/service-feedback {
        limit_conn ai_admin_office_conn 12;
        client_max_body_size 12m;
        proxy_pass http://127.0.0.1:8011;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Authorization "";
        add_header Cache-Control "private, no-store" always;
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    }

    location ^~ /office/assets/ {
        proxy_pass http://127.0.0.1:8011;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Authorization "";
        proxy_hide_header Set-Cookie;
        gzip on;
        gzip_types text/css application/javascript image/svg+xml;
        add_header Cache-Control "public, max-age=31536000, immutable" always;
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    }

    location ^~ /office/knowledge-assets/ {
        proxy_pass http://127.0.0.1:8011;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Authorization "";
        proxy_hide_header Set-Cookie;
        add_header Cache-Control "public, max-age=86400" always;
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    }

    location ^~ /office/ {
        limit_conn ai_admin_office_conn 20;
        client_max_body_size 1m;
        proxy_pass http://127.0.0.1:8011;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Authorization "";
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 330s;
        proxy_send_timeout 330s;
        add_header Cache-Control "private, no-store" always;
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
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
    index = opening
    while index < len(value):
        character = value[index]
        if escaped:
            escaped = False
        elif character == "\\" and quote is not None:
            escaped = True
        elif quote is not None:
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == "#":
            newline = value.find("\n", index)
            if newline < 0:
                break
            index = newline
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ValueError("Nginx server block invalid")


def _https_agent_server(value: str) -> tuple[int, int, str]:
    selected = []
    for match in _SERVER_START.finditer(value):
        opening = value.find("{", match.start())
        end = _matching_brace(value, opening)
        block = value[match.start():end]
        if (
            re.search(r"(?m)^\s*listen\s+443\s+ssl;\s*$", block)
            and re.search(
                r"(?m)^\s*server_name\s+agent\.orbbec\.com\.cn;\s*$",
                block,
            )
        ):
            selected.append((match.start(), end, block))
    if len(selected) != 1:
        raise ValueError("exact Agent HTTPS server required")
    return selected[0]


def _location_path(stripped: str) -> str | None:
    if not stripped.startswith("location"):
        return None
    if not stripped.endswith("{"):
        if "/admin" in stripped or "/office" in stripped:
            raise ValueError("unrecognized admin location selector")
        return None
    tokens = stripped[:-1].split()
    if len(tokens) < 2 or tokens[0] != "location":
        raise ValueError("Nginx location invalid")
    return tokens[-1]


def _transform_server(block: str) -> str:
    lines = block.splitlines(keepends=True)
    output: list[str] = []
    depth = 0
    removed_admin = 0
    roots = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if depth == 1:
            path = _location_path(line.strip())
            if path is not None:
                if path == "/office" or path.startswith("/office/"):
                    raise ValueError("existing office location is not allowed")
                if path == "/admin" or path.startswith("/admin/"):
                    removed_admin += 1
                    location_depth = depth
                    depth += _brace_delta(line)
                    index += 1
                    while index < len(lines) and depth > location_depth:
                        depth += _brace_delta(lines[index])
                        index += 1
                    if depth != location_depth:
                        raise ValueError("admin location invalid")
                    continue
                if "/admin" in line:
                    raise ValueError("unrecognized admin location selector")
                if path == "/":
                    roots += 1
                    if roots > 1:
                        raise ValueError("root location ambiguous")
                    output.append(_PLATFORM_ADMIN_LOCATIONS)
                    output.append(_OFFICE_LOCATIONS)
        output.append(line)
        depth += _brace_delta(line)
        if depth < 0:
            raise ValueError("Nginx server block invalid")
        index += 1
    if removed_admin < 1:
        raise ValueError("admin location boundary missing")
    if roots != 1:
        raise ValueError("root location boundary missing")
    if depth != 0:
        raise ValueError("Nginx server block invalid")
    return "".join(output)


def _validate_redacted_log(value: str) -> None:
    matches = list(_LOG_FORMAT.finditer(value))
    if len(matches) != 1:
        raise ValueError("exact redacted log format required")
    variables = tuple(re.findall(r"\$[A-Za-z0-9_]+", matches[0].group("body")))
    if variables != _EXPECTED_LOG_VARIABLES:
        raise ValueError("redacted log format contains unsafe variables")


def _add_missing_zones(value: str) -> str:
    missing: list[str] = []
    for directive in _ZONES:
        zone_name = re.search(r"zone=([^:;]+)", directive)
        assert zone_name is not None
        marker = f"zone={zone_name.group(1)}:"
        if directive in value:
            continue
        if marker in value:
            raise ValueError("AI ADMIN office limit zone is ambiguous")
        missing.append(directive)
    if not missing:
        return value
    first_server = _SERVER_START.search(value)
    if first_server is None:
        raise ValueError("Nginx server block invalid")
    insertion = "\n".join(missing) + "\n\n"
    return value[:first_server.start()] + insertion + value[first_server.start():]


def transform(value: str) -> str:
    _validate_redacted_log(value)
    start, end, block = _https_agent_server(value)
    transformed = value[:start] + _transform_server(block) + value[end:]
    return _add_missing_zones(transformed)


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
