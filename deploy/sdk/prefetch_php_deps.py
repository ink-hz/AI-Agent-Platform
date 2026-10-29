#!/usr/bin/env python3
"""At most four same-origin downloads, bound to already verified APT indexes."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote

ORIGINS = frozenset({
    "https://archive.debian.org/debian/",
    "https://archive.debian.org/debian-security/",
})


def parse_apt_plan(text: str, indexes: dict[str, str]) -> list[dict]:
    if not indexes or not set(indexes).issubset(ORIGINS):
        raise ValueError("package_identity_invalid")
    identities = {}
    for origin, index in indexes.items():
        for paragraph in index.split("\n\n"):
            fields = {}
            for line in paragraph.splitlines():
                if ": " in line and not line.startswith(" "):
                    key, value = line.split(": ", 1)
                    fields[key] = value
            if not {"Filename", "Size", "SHA256"}.issubset(fields):
                continue
            filename = fields["Filename"]
            if not filename.startswith("pool/") or ".." in filename.split("/"):
                raise ValueError("package_identity_invalid")
            identities[unquote(origin + filename)] = (int(fields["Size"]), fields["SHA256"])
    result = []
    names = set()
    for line in text.splitlines():
        if not line.startswith("'"):
            continue
        parts = shlex.split(line)
        if len(parts) not in {3, 4}:
            raise ValueError("package_identity_invalid")
        url, filename, raw_size = parts[:3]
        size, digest = identities.get(unquote(url), (None, None))
        if (size is None or int(raw_size) != size or not 0 < size <= 100 * 1024 * 1024
                or re.fullmatch(r"[A-Za-z0-9_.+%:~\-]+\.deb", filename) is None
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None or filename in names):
            raise ValueError("package_identity_invalid")
        names.add(filename)
        result.append({"url": url, "filename": filename, "size": size, "sha256": digest})
    if not result:
        raise ValueError("package_plan_empty")
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_packages(rows: list[dict], root: Path, *, workers: int = 4, opener=None) -> None:
    if not 1 <= workers <= 4:
        raise ValueError("download_concurrency_invalid")
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("package_cache_invalid")
    if opener is None:
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        opener = lambda url: client.open(url, timeout=30)
    failed = threading.Event()

    def download(row):
        if failed.is_set():
            raise ValueError("prefetch_cancelled")
        target = root / row["filename"]
        if target.is_symlink():
            failed.set()
            raise ValueError("package_cache_invalid")
        try:
            if target.exists():
                data_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                if target.stat().st_size != row["size"] or data_hash != row["sha256"]:
                    raise ValueError("package_cached_hash_mismatch")
                return
            digest = hashlib.sha256()
            size = 0
            partial = target.with_name(target.name + ".part")
            with partial.open("xb") as output, opener(row["url"]) as response:
                if response.status != 200:
                    raise ValueError("package_http_status_invalid")
                while True:
                    if failed.is_set():
                        raise ValueError("prefetch_cancelled")
                    block = response.read(65536)
                    if not block:
                        break
                    size += len(block)
                    if size > row["size"]:
                        raise ValueError("package_size_mismatch")
                    digest.update(block)
                    output.write(block)
            if size != row["size"] or digest.hexdigest() != row["sha256"]:
                raise ValueError("package_hash_mismatch")
            os.replace(partial, target)
            print("PACKAGE_VERIFIED " + row["filename"], flush=True)
        except Exception:
            failed.set()
            raise

    # Only four futures exist at once. A failed worker sets the stop event
    # immediately; active peers abort on their next bounded read, with no retry.
    iterator = iter(rows)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(download, row) for row in [next(iterator, None) for _ in range(workers)] if row}
        try:
            while pending:
                done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    future.result()
                for _ in done:
                    row = next(iterator, None)
                    if row is not None:
                        pending.add(pool.submit(download, row))
        except Exception:
            failed.set()
            for future in pending:
                future.cancel()
            raise


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root
    inputs = {
        origin: (root / filename).read_text()
        for origin, filename in [
            ("https://archive.debian.org/debian/", "apt-main.Packages"),
            ("https://archive.debian.org/debian-security/", "apt-security.Packages"),
        ]
    }
    plan_text = (root / "apt-download-plan.txt").read_text()
    rows = parse_apt_plan(plan_text, inputs)
    manifest = {
        "packages": rows,
        "apt_plan_sha256": hashlib.sha256(plan_text.encode()).hexdigest(),
        "index_content_sha256": {origin: hashlib.sha256(data.encode()).hexdigest() for origin, data in inputs.items()},
    }
    (root / "prefetch-plan.json").write_text(json.dumps(manifest, indent=2) + "\n")
    started = time.monotonic()
    print(f"PREFETCH_START packages={len(rows)} bytes={sum(row['size'] for row in rows)} connections=4", flush=True)
    fetch_packages(rows, root / "deb-cache")
    print(f"PREFETCH_COMPLETE elapsed_seconds={time.monotonic() - started:.3f}", flush=True)


if __name__ == "__main__":
    main()
