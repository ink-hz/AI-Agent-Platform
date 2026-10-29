#!/usr/bin/env python3
"""Exercise vendor external-DB startup using only new, private ephemeral targets."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

DB_IMAGE = "sha256:88ac718dafee1e8fb415233c04f314f3c3866af99177ce25d436106c62090461"


def check_runtime_maps(maps: str) -> None:
    for row in maps.splitlines():
        fields = row.split()
        if len(fields) >= 2 and fields[-1] == "(deleted)" and (
            fields[-2] == "/opt/zbox/run/apache/modules/libphp.so"
            or fields[-2].startswith(("/opt/zbox/run/php/extensions/", "/opt/zbox/php-security-lib/"))
        ):
            raise ValueError("live_php_mapping_deleted")
    paths = {row.split()[-1] for row in maps.splitlines() if row.split()}
    required = {"/opt/zbox/run/apache/modules/libphp.so", "/opt/zbox/run/php/extensions/opcache.so"}
    if not required <= paths or not any("/extensions/ioncube" in path for path in paths):
        raise ValueError("live_php_mapping_missing")


def docker(*args, input=None):
    return subprocess.check_output(["docker", *args], input=input, text=True).strip()


def inspect(cid):
    return json.loads(docker("inspect", "--format",
        '{"id":{{json .Id}},"image":{{json .Image}},"state":{{json .State}},'
        '"restarts":{{.RestartCount}},"resources":{"cpu":{{.HostConfig.NanoCpus}},'
        '"memory":{{.HostConfig.Memory}},"pids":{{.HostConfig.PidsLimit}}},'
        '"network":{{json .HostConfig.NetworkMode}},"ports":{{json .HostConfig.PortBindings}},'
        '"attempt_label":{{json (index .Config.Labels "org.orbbec.php-security.attempt")}}}', cid))


def wait_for(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise TimeoutError("entrypoint_readiness_timeout")


def startup_running(state):
    if state["Status"] == "created":
        return False
    if not state["Running"]:
        raise RuntimeError("vendor_startup_exited")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", args.image) is None:
        parser.error("immutable local candidate image ID required")
    attempt = Path(tempfile.mkdtemp(prefix="entrypoint-attempt-", dir="/data/security-build-20261029/php81"))
    evidence = {"status": "failed", "candidate": args.image, "db_image": DB_IMAGE}
    db = web = None
    startup = None
    password = secrets.token_hex(24)
    try:
        db = docker("run", "-d", "--name", "sdk-php-db-" + attempt.name.removeprefix("entrypoint-attempt-"),
            "--label", "org.orbbec.php-security.attempt=" + attempt.name,
            "--network", "none", "--read-only", "--no-healthcheck", "--log-driver", "none",
            "--cpus=1", "--memory=768m", "--memory-swap=768m", "--pids-limit=128", "--ulimit", "core=0",
            "--security-opt=no-new-privileges:true",
            "--tmpfs", "/var/lib/mysql:rw,nosuid,noexec,size=384m,uid=999,gid=999",
            "--tmpfs", "/run/mysqld:rw,nosuid,noexec,size=8m,uid=999,gid=999",
            "--tmpfs", "/tmp:rw,nosuid,noexec,size=64m", "--entrypoint", "/bin/sh", DB_IMAGE, "-c",
            "mariadb-install-db --user=mysql --datadir=/var/lib/mysql --skip-test-db "
            ">/tmp/install.log 2>&1 && exec mariadbd --user=mysql --datadir=/var/lib/mysql "
            "--bind-address=127.0.0.1 --port=3306 --skip-log-bin --skip-name-resolve "
            "--max-connections=10 --innodb-buffer-pool-size=64M --log-error=/tmp/db-error.log")

        def db_ready():
            if not inspect(db)["state"]["Running"]:
                raise RuntimeError("isolated_database_exited")
            return subprocess.run(["docker", "exec", db, "mariadb-admin", "--protocol=socket", "ping"],
                                  capture_output=True, check=False).returncode == 0

        wait_for(db_ready, 60)
        # Synthetic credential enters the isolated target over stdin only.
        docker("exec", "-i", db, "mariadb", "--protocol=socket", "-uroot", input=
            "CREATE DATABASE isolated_php_probe;\n"
            "CREATE USER 'isolated_php_probe'@'127.0.0.1' IDENTIFIED BY '" + password + "';\n"
            "GRANT ALL ON isolated_php_probe.* TO 'isolated_php_probe'@'127.0.0.1';\n")
        web = docker("create", "--interactive", "--name", "sdk-php-entry-" + attempt.name.removeprefix("entrypoint-attempt-"),
            "--label", "org.orbbec.php-security.attempt=" + attempt.name,
            "--network", "container:" + db, "--no-healthcheck", "--cpus=2", "--memory=3g", "--memory-swap=3g",
            "--pids-limit=256", "--ulimit", "core=0", "--security-opt=no-new-privileges:true",
            "--log-opt", "max-size=1m", "--log-opt", "max-file=1",
            "-e", "MYSQL_INTERNAL=false", "-e", "REDIS_INTERNAL=false", "-e", "RR_INTERNAL=false",
            "-e", "ZT_MYSQL_HOST=127.0.0.1", "-e", "ZT_MYSQL_PORT=3306",
            "-e", "ZT_MYSQL_DB=isolated_php_probe", "-e", "ZT_MYSQL_USER=isolated_php_probe",
            "-e", "ZT_WEB_ROOT=/sdk", "-e", "ZT_ADMINER_ENABLE=false", "--entrypoint", "/bin/bash", args.image,
            "-c", "IFS= read -r ZT_MYSQL_PASSWORD; export ZT_MYSQL_PASSWORD; "
            "touch /run/sdk-php-credential-consumed; exec /usr/local/bin/verify-and-start.sh")
        with (attempt / "startup.log").open("w") as log:
            startup = subprocess.Popen(["docker", "start", "-a", "-i", web], stdin=subprocess.PIPE, stdout=log, stderr=log, text=True)
            startup.stdin.write(password + "\n")
            startup.stdin.flush()

            def apache_ready():
                state = inspect(web)
                if state["image"] != args.image or state["attempt_label"] != attempt.name:
                    raise ValueError("entrypoint_target_identity_invalid")
                if not startup_running(state["state"]):
                    return False
                handshake = subprocess.run(["docker", "exec", web, "test", "-f", "/run/sdk-php-credential-consumed"],
                                           capture_output=True, check=False)
                if handshake.returncode != 0:
                    return False
                return subprocess.run(["docker", "exec", web, "/opt/zbox/bin/php", "-r",
                    '$s=@fsockopen("127.0.0.1",80,$e,$m,0.2);if(!$s)exit(1);fclose($s);'],
                    capture_output=True, check=False).returncode == 0

            wait_for(apache_ready, 60)
            if "PHP_RUNTIME_GUARD_PASSED" not in docker("logs", web):
                raise ValueError("vendor_final_guard_marker_missing")
            evidence["credential_consumed"] = True
            evidence["vendor_final_guard_marker"] = True
            evidence["runtime_integrity"] = docker("exec", web, "/usr/local/bin/verify-and-start.sh", "--verify-only")
            maps = docker("exec", web, "sh", "-c", 'p=$(cat /opt/zbox/tmp/apache/httpd.pid); '
                          'test "$(readlink /proc/$p/exe)" = /opt/zbox/run/apache/httpd; cat /proc/$p/maps')
            (attempt / "apache.maps").write_text(maps + "\n")
            check_runtime_maps(maps)
            evidence["runtime_sha256"] = docker("exec", web, "sha256sum", "/opt/zbox/run/php/php",
                "/opt/zbox/run/apache/modules/libphp.so", "/opt/zbox/run/php/extensions/opcache.so",
                "/opt/zbox/etc/php/conf.d/99-sdk-security.ini")
            evidence["config_sha256"] = docker("exec", web, "sha256sum", "/apps/zentao/config/config.php",
                "/opt/zbox/etc/php/php.ini", "/opt/zbox/etc/apache/httpd.conf")
            probe_name = "__sdk_runtime_probe_" + secrets.token_hex(8) + ".php"
            docker("exec", web, "cp", "/usr/local/share/sdk-php/check-php-runtime.php", "/apps/zentao/www/" + probe_name)
            evidence["http_runtime"] = json.loads(docker("exec", web, "curl", "--silent", "--show-error", "--fail",
                "--max-time", "5", "http://127.0.0.1/sdk/" + probe_name))
            if evidence["http_runtime"].get("status") != "ok" or evidence["http_runtime"].get("sapi") != "apache2handler":
                raise ValueError("entrypoint_http_runtime_invalid")
            evidence["db"] = inspect(db)
            evidence["web"] = inspect(web)
            namespaces = {key: os.readlink(f"/proc/{evidence[key]['state']['Pid']}/ns/net") for key in ("db", "web")}
            if namespaces["db"] != namespaces["web"] or namespaces["db"] == os.readlink("/proc/1/ns/net"):
                raise ValueError("entrypoint_network_namespace_invalid")
            evidence["network_namespaces"] = namespaces
            for cid in (db, web):
                state = inspect(cid)
                if state["state"]["OOMKilled"] or state["restarts"] != 0:
                    raise ValueError("entrypoint_container_restarted_or_oom")
            evidence["status"] = "ok"
    except Exception as error:
        evidence["failure"] = str(error)
        raise
    finally:
        for key, cid in (("web", web), ("db", db)):
            if cid is not None:
                evidence[key + "_final"] = inspect(cid)
                subprocess.run(["docker", "rm", "-fv", cid], check=True, stdout=subprocess.DEVNULL)
        if startup is not None:
            startup.wait(timeout=10)
            evidence["attachment_client_exit_after_cleanup"] = startup.returncode
        (attempt / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(attempt, flush=True)


if __name__ == "__main__":
    main()
