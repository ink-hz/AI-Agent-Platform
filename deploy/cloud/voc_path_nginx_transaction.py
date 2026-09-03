#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
import time


_SERVER_START = re.compile(r"(?m)^server\s*\{")
_SOURCE = Path("/etc/nginx/sites-available/agent-domain.conf")
_LOCK_PATH = Path(
    "/opt/orbbec-agent-platform/private/deploy-input.transaction.lock"
)
_INPUT_LOCK_PATH = Path("/opt/orbbec-agent-platform/private/deploy-input.lock")
_BACKUP_PREFIX = "/root/nginx-backups/voc-path-"
_NGINX = "/usr/sbin/nginx"
_SYSTEMCTL = "/bin/systemctl"
_ROOT_UID = 0
_ROOT_GID = 0

_SECURITY_HEADERS = """\
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
"""

_VOC_LOCATIONS = f"""\
    location = /voc {{
        return 308 /voc/$is_args$args;
        add_header Cache-Control "private, no-store" always;
{_SECURITY_HEADERS}    }}

    location = /voc/health {{
        return 404;
        add_header Cache-Control "private, no-store" always;
{_SECURITY_HEADERS}    }}

    location ^~ /voc/assets/ {{
        client_max_body_size 1m;
        proxy_pass http://172.29.0.3:18130;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Authorization "";
        proxy_set_header Connection "";
        proxy_hide_header Set-Cookie;
        proxy_hide_header Cache-Control;
        gzip on;
        gzip_vary on;
        gzip_min_length 1024;
        gzip_types text/css application/javascript text/javascript application/json image/svg+xml font/woff font/woff2;
        add_header Cache-Control "public, max-age=31536000, immutable" always;
{_SECURITY_HEADERS}    }}

    location ^~ /voc/ {{
        client_max_body_size 1m;
        proxy_pass http://172.29.0.3:18130;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header Authorization "";
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_cache off;
        proxy_read_timeout 330s;
        proxy_send_timeout 330s;
        add_header Cache-Control "private, no-store" always;
{_SECURITY_HEADERS}    }}

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
    selected: list[tuple[int, int, str]] = []
    for match in _SERVER_START.finditer(value):
        opening = value.find("{", match.start())
        end = _matching_brace(value, opening)
        block = value[match.start() : end]
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


def _location_path(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("location") or "{" not in stripped:
        return None
    selector = stripped.split("{", 1)[0].split()
    if len(selector) < 2 or selector[0] != "location":
        raise ValueError("Nginx location invalid")
    return selector[-1]


def _transform_server(block: str) -> str:
    lines = block.splitlines(keepends=True)
    output: list[str] = []
    depth = 0
    roots = 0
    for line in lines:
        if depth == 1:
            path = _location_path(line)
            if path is not None:
                if "/voc" in line:
                    raise ValueError("existing VOC location is not allowed")
                if path == "/":
                    roots += 1
                    if roots > 1:
                        raise ValueError("root location ambiguous")
                    output.append(_VOC_LOCATIONS)
        output.append(line)
        depth += _brace_delta(line)
        if depth < 0:
            raise ValueError("Nginx server block invalid")
    if roots != 1:
        raise ValueError("root location boundary missing")
    if depth != 0:
        raise ValueError("Nginx server block invalid")
    return "".join(output)


def _validate_transformed(value: str) -> None:
    _, _, block = _https_agent_server(value)
    selectors = (
        "location = /voc {",
        "location = /voc/health {",
        "location ^~ /voc/assets/ {",
        "location ^~ /voc/ {",
    )
    positions = []
    for selector in selectors:
        if block.count(selector) != 1:
            raise ValueError("exact VOC location set required")
        positions.append(block.index(selector))
    if positions != sorted(positions):
        raise ValueError("VOC location order invalid")
    root = block.find("location / {", positions[-1])
    if root < 0:
        raise ValueError("root location boundary missing")
    if block.count("proxy_pass http://172.29.0.3:18130;") != 2:
        raise ValueError("VOC upstream boundary invalid")


def transform(value: str) -> str:
    start, end, block = _https_agent_server(value)
    rendered = value[:start] + _transform_server(block) + value[end:]
    _validate_transformed(rendered)
    return rendered


def _write_exclusive(path: Path, value: bytes, mode: int) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        os.fchown(descriptor, _ROOT_UID, _ROOT_GID)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _atomic_install(path: Path, value: bytes) -> None:
    candidate = path.with_name(f".{path.name}.voc-path.{os.getpid()}.part")
    _write_exclusive(candidate, value, 0o644)
    try:
        os.replace(candidate, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def _read_source(path: Path) -> tuple[bytes, str]:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != _ROOT_UID
        or metadata.st_gid != _ROOT_GID
        or stat.S_IMODE(metadata.st_mode) != 0o644
    ):
        raise ValueError("Nginx source path invalid")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("Nginx source changed while opening")
        value = os.read(descriptor, 2_000_001)
        if not value or len(value) > 2_000_000:
            raise ValueError("Nginx source size invalid")
    finally:
        os.close(descriptor)
    try:
        return value, value.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Nginx source encoding invalid") from None


def run_transaction(source: Path = _SOURCE) -> Path:
    if os.geteuid() != _ROOT_UID:
        raise PermissionError("VOC Nginx transaction requires root")
    lock_descriptor = os.open(
        _LOCK_PATH,
        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        lock_metadata = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != _ROOT_UID
            or lock_metadata.st_gid != _ROOT_GID
            or stat.S_IMODE(lock_metadata.st_mode) != 0o600
        ):
            raise ValueError("deploy transaction lock invalid")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if _INPUT_LOCK_PATH.exists() or _INPUT_LOCK_PATH.is_symlink():
            raise RuntimeError("another Platform deployment is active")
        original_bytes, original = _read_source(source)
        _, _, https_server = _https_agent_server(original)
        rendered = transform(original).encode("utf-8")

        backup_root = Path(_BACKUP_PREFIX).parent
        backup_root_metadata = backup_root.lstat()
        if (
            backup_root.is_symlink()
            or not stat.S_ISDIR(backup_root_metadata.st_mode)
            or backup_root_metadata.st_uid != _ROOT_UID
            or backup_root_metadata.st_gid != _ROOT_GID
            or stat.S_IMODE(backup_root_metadata.st_mode) != 0o700
        ):
            raise ValueError("Nginx backup root invalid")
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = Path(f"{_BACKUP_PREFIX}{stamp}-{os.getpid()}")
        backup.mkdir(mode=0o700, parents=False, exist_ok=False)
        os.chown(backup, _ROOT_UID, _ROOT_GID)
        _write_exclusive(backup / "agent-domain.conf", original_bytes, 0o600)
        _write_exclusive(
            backup / "https-server.conf", https_server.encode("utf-8"), 0o600
        )

        installed = False
        try:
            _atomic_install(source, rendered)
            installed = True
            subprocess.run([_NGINX, "-t"], check=True)
            subprocess.run([_SYSTEMCTL, "reload", "nginx"], check=True)
        except BaseException:
            if installed:
                _atomic_install(source, original_bytes)
                subprocess.run([_NGINX, "-t"], check=True)
                subprocess.run([_SYSTEMCTL, "reload", "nginx"], check=True)
            raise
        return backup
    finally:
        os.close(lock_descriptor)


def main() -> int:
    backup = run_transaction()
    print(f"VOC_NGINX_TRANSACTION_OK backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
