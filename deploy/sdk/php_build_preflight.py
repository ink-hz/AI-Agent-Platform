#!/usr/bin/env python3
"""Audit the entire captured vendor capability/ABI contract before configure."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# Each baseline configure capability has an explicit implementation boundary.
# Empty lists mean PHP/core code; PEAR's existing runtime tree is retained and
# this build never runs make install or downloads PEAR independently.
FEATURES = {
    "--enable-mbstring": (["oniguruma"], []),
    "--with-zip": (["libzip"], []),
    "--with-ffi": (["libffi"], []),
    "--enable-bcmath": ([], []), "--enable-pcntl": ([], []),
    "--enable-ftp": ([], []), "--enable-exif": ([], []),
    "--enable-calendar": ([], []),
    "--with-imap": ([], ["/usr/include/c-client/c-client.h", "/usr/lib/libc-client.a",
                         "/usr/include/security/pam_appl.h", "/usr/lib/x86_64-linux-gnu/libpam.so",
                         "/usr/include/crypt.h", "/usr/lib/x86_64-linux-gnu/libcrypt.so"]),
    "--with-imap-ssl": (["openssl"], []),
    "--with-gettext": ([], ["/usr/include/libintl.h"]),
    "--enable-gd": (["libpng"], []),
    "--with-freetype": (["freetype2"], []),
    "--with-jpeg": (["libjpeg"], []),
    "--with-webp": (["libwebp"], []),
    "--with-zlib": (["zlib"], []),
    "--with-libxml": (["libxml-2.0"], []),
    "--with-openssl": (["openssl"], []),
    "--with-curl": (["libcurl"], []),
    "--with-bz2": ([], ["/usr/include/bzlib.h", "/usr/lib/x86_64-linux-gnu/libbz2.so"]),
    "--with-readline": (["ncurses"], ["/usr/include/readline/readline.h", "/usr/lib/x86_64-linux-gnu/libreadline.so"]),
    "--with-ldap": ([], ["/usr/include/ldap.h", "/usr/lib/x86_64-linux-gnu/libldap.so", "/usr/lib/x86_64-linux-gnu/liblber.so"]),
    "--with-ldap-sasl": (["libsasl2"], []),
    "--with-mysqli": ([], []), "--with-pdo-mysql": ([], []),
    "--with-pear": ([], []),
    "--without-sqlite3": ([], []), "--without-pdo-sqlite": ([], []),
    "--enable-opcache": ([], []),
    "--enable-intl": (["icu-uc", "icu-io", "icu-i18n"], []),
    "--with-kerberos": (["krb5-gssapi", "krb5"], []),
    "--disable-cgi": ([], []),
}
LAYOUT = {
    "--build", "--prefix", "--sysconfdir", "--includedir",
    "--with-config-file-path", "--with-config-file-scan-dir",
    "--with-apxs2", "--with-libdir",
}
ADAPTATIONS = {
    "--with-apxs2": ("/opt/zbox/run/apache/bin/apxs", "/usr/bin/apxs"),
    "--with-libdir": ("lib64", "lib"),
}
TOOLS = ("cc", "g++", "make", "autoconf", "bison", "re2c", "pkg-config")


def _options(args):
    result = {}
    for arg in args:
        if not arg.startswith("--"):
            continue
        key, _, value = arg.partition("=")
        if key in result:
            raise ValueError("configure_duplicate_option")
        result[key] = value
    return result


def check_configure_contract(baseline_options: str, args: list[str]) -> None:
    before = _options(shlex.split(baseline_options))
    after = _options(args)
    if set(before) - (set(FEATURES) | LAYOUT) or set(after) - (set(FEATURES) | LAYOUT):
        raise ValueError("configure_capability_unknown")
    if set(before) != set(after):
        raise ValueError("configure_capability_mismatch")
    for key, value in before.items():
        if key in ADAPTATIONS:
            if (value, after[key]) != ADAPTATIONS[key]:
                raise ValueError("configure_value_mismatch:" + key)
        elif value != after[key]:
            raise ValueError("configure_value_mismatch:" + key)


def missing_dependencies(args, pkg_exists, path_exists, tool_exists) -> list[str]:
    selected = _options(args)
    modules = set()
    paths = {"/usr/bin/apxs"}
    for key in selected:
        if key in FEATURES:
            pkg_modules, headers = FEATURES[key]
            modules.update(pkg_modules)
            paths.update(headers)
    missing = ["pkg:" + name for name in sorted(modules) if not pkg_exists(name)]
    missing += ["file:" + path for path in sorted(paths) if not path_exists(path)]
    missing += ["tool:" + name for name in TOOLS if not tool_exists(name)]
    return missing


def verify_baseline_runtime(root: Path, baseline: dict, actual: dict) -> None:
    if (actual["version"] != baseline["PHP Version"] or actual["api"] != baseline["PHP API"]
            or actual["zend"] != baseline["Zend Extension Build"] or actual["zts"] != 0):
        raise ValueError("baseline_runtime_abi")
    section = baseline["modules"].split("[PHP Modules]", 1)[1].split("[Zend Modules]", 1)[0]
    expected_modules = {name.strip().casefold() for name in section.splitlines() if name.strip()}
    if expected_modules != {name.casefold() for name in actual["modules"]}:
        raise ValueError("baseline_runtime_modules")
    hashes = {}
    fixed = {"/opt/zbox/run/php/php", "/opt/zbox/run/apache/modules/libphp.so"}
    for line in baseline["binary_sha256"].splitlines():
        digest, path = line.split(maxsplit=1)
        if (path not in fixed and re.fullmatch(r"/opt/zbox/run/php/extensions/[A-Za-z0-9_.-]+\.so", path) is None
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None or path in hashes):
            raise ValueError("baseline_runtime_inventory")
        hashes[path] = digest
    if not (fixed | {"/opt/zbox/run/php/extensions/opcache.so"}) <= set(hashes):
        raise ValueError("baseline_runtime_inventory")
    actual_extensions = {"/" + str(path.relative_to(root)) for path in (root / "opt/zbox/run/php/extensions").glob("*.so")}
    if actual_extensions != set(hashes) - fixed:
        raise ValueError("baseline_runtime_inventory")
    for path, digest in hashes.items():
        target = root / path.lstrip("/")
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("baseline_runtime_hash:" + path)


def verify_generated_config(header: str, target_mpm: str) -> None:
    if (re.search(r"^\s*#\s*define\s+ZTS\b", header, re.MULTILINE)
            or not re.search(r"/\*\s*#undef\s+ZTS\s*\*/", header)):
        raise ValueError("generated_php_zts")
    if not re.search(r"Server MPM:\s+prefork\b", target_mpm) or not re.search(r"threaded:\s+no\b", target_mpm):
        raise ValueError("target_apache_mpm")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/build")
    baseline = json.loads((root / "baseline.json").read_text())
    lock = json.loads((root / "php-source.json").read_text())
    if len(sys.argv) > 2 and sys.argv[2] == "--generated":
        header = (root / "work" / ("php-src-" + lock["source_commit"]) / "main/php_config.h").read_text()
        target_mpm = subprocess.check_output(["/opt/zbox/run/apache/httpd", "-V"], text=True, stderr=subprocess.STDOUT)
        verify_generated_config(header, target_mpm)
        apxs = {name: subprocess.run(["/usr/bin/apxs", "-q", name], capture_output=True, text=True, check=False).stdout.strip()
                for name in ("SBINDIR", "TARGET", "INCLUDEDIR")}
        print(json.dumps({"status": "ok", "generated_zts": False, "target_apache_version_and_mpm": target_mpm,
                          "builder_apxs": apxs, "builder_mpm_discovery": "unavailable" if not apxs["TARGET"] else "separate_apxs_target"}, indent=2))
        return
    if baseline["image"] != lock["base_image"]:
        raise ValueError("baseline_image_mismatch")
    args = (root / "php-configure-args.txt").read_text().splitlines()
    check_configure_contract(baseline["Configure Command"], args)
    code = r'''ob_start();phpinfo(INFO_GENERAL);$s=ob_get_clean();
preg_match('/PHP API\s*=>\s*(\d+)/',$s,$a);
preg_match('/Zend Extension Build\s*=>\s*(\S+)/',$s,$z);
echo json_encode(['version'=>PHP_VERSION,'zts'=>PHP_ZTS,'api'=>$a[1]??null,
'zend'=>$z[1]??null,'modules'=>get_loaded_extensions()]);'''
    process = subprocess.run(["/opt/zbox/bin/php", "-r", code], capture_output=True, text=True, check=True)
    if process.stderr:
        raise ValueError("baseline_php_startup_warning")
    actual = json.loads(process.stdout)
    verify_baseline_runtime(Path("/"), baseline, actual)
    versions = {}

    def pkg_exists(name):
        result = subprocess.run(["pkg-config", "--modversion", name], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            versions[name] = result.stdout.strip()
        return result.returncode == 0

    missing = missing_dependencies(args, pkg_exists, lambda path: Path(path).is_file(), lambda name: shutil.which(name) is not None)
    print(json.dumps({"status": "failed" if missing else "ok", "missing": missing,
                      "configure_flags": len(args), "baseline_modules": len(actual["modules"]),
                      "baseline_php_api": actual["api"], "pkg_config_versions": versions}, indent=2))
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
