"""The PHP candidate must bind audited source to every executable artifact."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shlex
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest

SDK = Path(__file__).parents[2] / "deploy" / "sdk"


def verifier():
    path = SDK / "verify_php_artifacts.py"
    assert path.is_file(), "the PHP artifact validator is required before packaging"
    spec = importlib.util.spec_from_file_location("sdk_php_artifacts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def artifacts(tmp_path):
    lock = {
        "source_commit": "1d7dec178a094e6e75b229ce22754045f8c6070a",
        "source_sha256": "a" * 64,
        "php_version": "8.1.34",
        "php_api": "20210902",
        "zend_extension_build": "API420210902,NTS",
        "base_image": "sha256:1deeb004b41a691ef45fd8670a4794ef446bb3c0362e402a2e87a705cf26d5ce",
    }
    manifest = dict(lock)
    manifest["files"] = {}
    for name in ("php", "libphp.so", "opcache.so"):
        content = ("fixture-" + name).encode()
        (tmp_path / name).write_bytes(content)
        manifest["files"][name] = hashlib.sha256(content).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path, lock, manifest


def test_complete_candidate_binds_both_sapis_and_opcache(artifacts):
    root, lock, _ = artifacts
    result = verifier().verify_artifacts(root, lock)
    assert result["source_commit"] == lock["source_commit"]
    assert set(result["files"]) == {"php", "libphp.so", "opcache.so"}


@pytest.mark.parametrize("name", ["php", "libphp.so", "opcache.so"])
def test_corruption_of_any_executable_is_rejected(artifacts, name):
    root, lock, _ = artifacts
    (root / name).write_bytes(b"old unpatched runtime")
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        verifier().verify_artifacts(root, lock)


@pytest.mark.parametrize("field,value", [
    ("source_commit", "f" * 40),
    ("source_sha256", "f" * 64),
    ("php_version", "8.1.31"),
    ("php_api", "20220829"),
    ("zend_extension_build", "API420210902,TS"),
    ("base_image", "sha256:" + "0" * 64),
])
def test_source_version_abi_and_base_must_match_the_reviewed_lock(artifacts, field, value):
    root, lock, manifest = artifacts
    manifest[field] = value
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="provenance_mismatch"):
        verifier().verify_artifacts(root, lock)


def test_missing_web_sapi_is_not_accepted_as_a_cli_only_fix(artifacts):
    root, lock, manifest = artifacts
    del manifest["files"]["libphp.so"]
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="artifact_set_invalid"):
        verifier().verify_artifacts(root, lock)


@pytest.mark.parametrize("replacement", ["../libphp.so", "/tmp/libphp.so", "nested/libphp.so"])
def test_receipt_cannot_select_arbitrary_paths(artifacts, replacement):
    root, lock, manifest = artifacts
    manifest["files"][replacement] = manifest["files"].pop("libphp.so")
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="artifact_set_invalid"):
        verifier().verify_artifacts(root, lock)


def test_symlink_artifact_is_rejected_even_when_its_target_hash_matches(artifacts):
    root, lock, _ = artifacts
    (root / "libphp.so").rename(root / "outside.so")
    (root / "libphp.so").symlink_to(root / "outside.so")
    with pytest.raises(ValueError, match="artifact_not_regular"):
        verifier().verify_artifacts(root, lock)


def test_duplicate_receipt_fields_are_rejected(artifacts):
    root, lock, manifest = artifacts
    encoded = json.dumps(manifest)
    (root / "manifest.json").write_text('{"source_commit":"bad",' + encoded[1:])
    with pytest.raises(ValueError, match="manifest_duplicate_key"):
        verifier().verify_artifacts(root, lock)


def test_receipt_can_bind_private_runtime_libraries_and_reject_their_corruption(artifacts):
    root, lock, manifest = artifacts
    (root / "lib").mkdir()
    library = root / "lib" / "libzip.so.4"
    library.write_bytes(b"reviewed dependency")
    manifest["files"]["lib/libzip.so.4"] = hashlib.sha256(library.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))
    verifier().verify_artifacts(root, lock)
    library.write_bytes(b"substituted dependency")
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        verifier().verify_artifacts(root, lock)


def test_library_parent_cannot_redirect_hash_validation_outside_the_artifact_set(artifacts):
    root, lock, manifest = artifacts
    outside = root / "outside"
    outside.mkdir()
    content = b"elsewhere"
    (outside / "libzip.so.4").write_bytes(content)
    (root / "lib").symlink_to(outside, target_is_directory=True)
    manifest["files"]["lib/libzip.so.4"] = hashlib.sha256(content).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="artifact_not_regular"):
        verifier().verify_artifacts(root, lock)


def test_unlisted_library_cannot_be_copied_into_the_runtime_search_path(artifacts):
    root, lock, _ = artifacts
    (root / "lib").mkdir()
    (root / "lib" / "libcrypto.so.1.1").write_bytes(b"unreviewed preload candidate")
    with pytest.raises(ValueError, match="artifact_set_invalid"):
        verifier().verify_artifacts(root, lock)


def test_startup_checks_runtime_before_running_legacy_entrypoint():
    text = (SDK / "verify-and-start.sh").read_text()
    assert "runtime.sha256" in text, "startup must reject substituted CLI/Web/OPcache bytes"
    assert text.index("runtime.sha256") < text.index('exec /usr/bin/entrypoint.sh "$@"')
    assert "9c6710dab686d7c39f8e3e49f9ea91ab575fc51c384bd9dd3d19ac12eb883b88" in text


def prefetcher():
    path = SDK / "prefetch_php_deps.py"
    assert path.is_file(), "parallel prefetch must retain the signed APT package identity"
    spec = importlib.util.spec_from_file_location("sdk_php_prefetch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def package_plan(*, url=None, size=4, digest=None):
    origin = "https://archive.debian.org/debian/"
    url = url or origin + "pool/main/d/demo/demo_1_amd64.deb"
    digest = digest or hashlib.sha256(b"demo").hexdigest()
    indexes = {origin: "Package: demo\nVersion: 1\nFilename: pool/main/d/demo/demo_1_amd64.deb\n"
               f"Size: 4\nSHA256: {digest}\n\n"}
    plan = f"'{url}' demo_1_amd64.deb {size} MD5Sum:deliberately-untrusted\n"
    return plan, indexes


def test_prefetch_uses_signed_index_sha256_instead_of_print_uris_md5():
    plan, indexes = package_plan()
    rows = prefetcher().parse_apt_plan(plan, indexes)
    assert rows[0]["sha256"] == hashlib.sha256(b"demo").hexdigest()
    assert rows[0]["size"] == 4


def test_sha256_only_security_index_does_not_require_optional_print_uris_md5():
    plan, indexes = package_plan()
    plan = plan.removesuffix(" MD5Sum:deliberately-untrusted\n") + "\n"
    rows = prefetcher().parse_apt_plan(plan, indexes)
    assert rows[0]["sha256"] == hashlib.sha256(b"demo").hexdigest()


@pytest.mark.parametrize("url", [
    "https://evil.example/demo.deb",
    "https://archive.debian.org/debian/pool/unknown.deb",
])
def test_prefetch_cannot_change_origin_or_unsigned_package_identity(url):
    plan, indexes = package_plan(url=url)
    with pytest.raises(ValueError, match="package_identity_invalid"):
        prefetcher().parse_apt_plan(plan, indexes)


def test_prefetch_rejects_plan_size_different_from_signed_index():
    plan, indexes = package_plan(size=5)
    with pytest.raises(ValueError, match="package_identity_invalid"):
        prefetcher().parse_apt_plan(plan, indexes)


def test_prefetch_first_hash_failure_stops_without_retry_or_success_marker(tmp_path):
    plan, indexes = package_plan()
    module = prefetcher()
    rows = module.parse_apt_plan(plan, indexes)
    calls = []

    class Response(BytesIO):
        status = 200

    def wrong_bytes(url):
        calls.append(url)
        return Response(b"evil")

    with pytest.raises(ValueError, match="package_hash_mismatch"):
        module.fetch_packages(rows, tmp_path, workers=1, opener=wrong_bytes)
    assert len(calls) == 1
    assert not (tmp_path / rows[0]["filename"]).exists()


def test_prefetch_reuses_only_size_and_sha256_verified_cache(tmp_path):
    plan, indexes = package_plan()
    module = prefetcher()
    rows = module.parse_apt_plan(plan, indexes)
    (tmp_path / rows[0]["filename"]).write_bytes(b"demo")

    def network_forbidden(url):
        pytest.fail("verified cached packages must not be downloaded again")

    module.fetch_packages(rows, tmp_path, opener=network_forbidden)


def build_preflight():
    path = SDK / "php_build_preflight.py"
    assert path.is_file(), "all baseline capabilities and build dependencies need one preflight gate"
    spec = importlib.util.spec_from_file_location("sdk_php_preflight", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline_options():
    args = (SDK / "php-configure-args.txt").read_text().splitlines()
    old = [arg.replace("--with-apxs2=/usr/bin/apxs", "--with-apxs2=/opt/zbox/run/apache/bin/apxs")
           .replace("--with-libdir=lib", "--with-libdir=lib64") for arg in args]
    return shlex.join(["./configure", *old]), args


@pytest.mark.parametrize("missing", ["--with-pear", "--with-ldap-sasl", "--enable-ftp"])
def test_build_contract_cannot_silently_drop_any_baseline_capability(missing):
    baseline, args = baseline_options()
    args.remove(missing)
    with pytest.raises(ValueError, match="configure_capability_mismatch"):
        build_preflight().check_configure_contract(baseline, args)


def test_build_contract_accepts_only_the_two_reviewed_layout_adaptations():
    baseline, args = baseline_options()
    build_preflight().check_configure_contract(baseline, args)
    args = [arg.replace("--with-libdir=lib", "--with-libdir=lib/x86_64-linux-gnu") for arg in args]
    with pytest.raises(ValueError, match="configure_value_mismatch"):
        build_preflight().check_configure_contract(baseline, args)


def test_preflight_reports_all_missing_packages_instead_of_failing_one_at_a_time():
    _, args = baseline_options()
    seen = []

    def pkg_exists(name):
        seen.append(name)
        return name not in {"libsasl2", "libzip"}

    missing = build_preflight().missing_dependencies(args, pkg_exists, lambda _: True, lambda _: True)
    assert set(missing) == {"pkg:libsasl2", "pkg:libzip"}
    assert {"openssl", "libcurl", "icu-uc", "oniguruma", "zlib", "libsasl2", "libzip"} <= set(seen)


def test_preflight_reports_manual_header_and_library_contracts_together():
    _, args = baseline_options()
    absent = {"/usr/lib/libc-client.a", "/usr/include/security/pam_appl.h"}
    missing = build_preflight().missing_dependencies(args, lambda _: True, lambda p: p not in absent, lambda _: True)
    assert set(missing) == {"file:" + name for name in absent}


def test_preflight_rejects_unknown_configure_features_for_explicit_review():
    baseline, args = baseline_options()
    with pytest.raises(ValueError, match="configure_capability_unknown"):
        build_preflight().check_configure_contract(baseline + " --enable-new-provider", args + ["--enable-new-provider"])


@pytest.fixture
def baseline_runtime(tmp_path):
    paths = ["/opt/zbox/run/php/php", "/opt/zbox/run/apache/modules/libphp.so",
             "/opt/zbox/run/php/extensions/opcache.so", "/opt/zbox/run/php/extensions/ioncube_loader.so"]
    checksums = []
    for path in paths:
        target = tmp_path / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.encode())
        checksums.append(hashlib.sha256(target.read_bytes()).hexdigest() + "  " + path)
    baseline = {"binary_sha256": "\n".join(checksums), "PHP Version": "8.1.31",
                "PHP API": "20210902", "Zend Extension Build": "API420210902,NTS",
                "modules": "[PHP Modules]\nCore\nionCube Loader\nZend OPcache\n[Zend Modules]\nZend OPcache"}
    actual = {"version": "8.1.31", "api": "20210902", "zend": "API420210902,NTS", "zts": 0,
              "modules": ["Core", "ionCube Loader", "Zend OPcache"]}
    return tmp_path, baseline, actual


def test_preflight_binds_all_preserved_module_bytes_and_the_original_abi(baseline_runtime):
    root, baseline, actual = baseline_runtime
    build_preflight().verify_baseline_runtime(root, baseline, actual)
    (root / "opt/zbox/run/php/extensions/ioncube_loader.so").write_bytes(b"changed")
    with pytest.raises(ValueError, match="baseline_runtime_hash"):
        build_preflight().verify_baseline_runtime(root, baseline, actual)


def test_preflight_does_not_accept_lost_extension_or_different_php_abi(baseline_runtime):
    root, baseline, actual = baseline_runtime
    actual["modules"].remove("ionCube Loader")
    with pytest.raises(ValueError, match="baseline_runtime_modules"):
        build_preflight().verify_baseline_runtime(root, baseline, actual)
    actual["modules"].append("ionCube Loader")
    actual["api"] = "20220829"
    with pytest.raises(ValueError, match="baseline_runtime_abi"):
        build_preflight().verify_baseline_runtime(root, baseline, actual)


def test_make_gate_rejects_generated_zts_before_compilation():
    module = build_preflight()
    assert hasattr(module, "verify_generated_config"), "generated thread mode needs a gate before make"
    with pytest.raises(ValueError, match="generated_php_zts"):
        module.verify_generated_config("#define ZTS 1\n", "Server MPM: prefork\n threaded: no\n")


def test_make_gate_requires_both_explicit_nts_header_and_target_prefork():
    module = build_preflight()
    assert hasattr(module, "verify_generated_config"), "generated thread mode needs a gate before make"
    module.verify_generated_config("/* #undef ZTS */\n", "Server MPM: prefork\n threaded: no\n")
    with pytest.raises(ValueError, match="generated_php_zts"):
        module.verify_generated_config("/* absent marker */", "Server MPM: prefork\n threaded: no\n")
    with pytest.raises(ValueError, match="target_apache_mpm"):
        module.verify_generated_config("/* #undef ZTS */\n", "Server MPM: event\n threaded: yes\n")


@pytest.fixture
def package_context(artifacts):
    root, lock, manifest = artifacts
    package = root / "package"
    runtime = package / "php-runtime"
    runtime.mkdir(parents=True)
    for name in ["php", "libphp.so", "opcache.so", "manifest.json"]:
        shutil.copyfile(root / name, runtime / name)
    for name in ["Dockerfile.hardened", "verify-and-start.sh", "php-security.ini",
                 "check-php-runtime.php", "disable-edit-comment.php", "probe-php-apache.sh"]:
        shutil.copyfile(SDK / name, package / name)
    (package / "php-source.json").write_text(json.dumps(lock))
    (runtime / "artifact.sha256").write_text("0" * 64 + "  php\n")
    return package, lock, manifest


def test_packager_generates_checksum_from_the_verified_actual_manifest(package_context):
    package, lock, manifest = package_context
    module = verifier()
    assert hasattr(module, "prepare_package"), "the actual Docker context needs an enforced verification entry"
    module.prepare_package(package, lock)
    expected = "".join(digest + "  " + name + "\n" for name, digest in sorted(manifest["files"].items()))
    assert (package / "php-runtime/artifact.sha256").read_text() == expected
    assert json.loads((package / "package-receipt.json").read_text())["source_commit"] == lock["source_commit"]


def test_packager_rejects_changed_bytes_even_if_independent_checksum_was_replaced(package_context):
    package, lock, _ = package_context
    module = verifier()
    assert hasattr(module, "prepare_package"), "the actual Docker context needs an enforced verification entry"
    binary = package / "php-runtime/libphp.so"
    binary.write_bytes(b"substituted web PHP")
    (package / "php-runtime/artifact.sha256").write_text(hashlib.sha256(binary.read_bytes()).hexdigest() + "  libphp.so\n")
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        module.prepare_package(package, lock)


def test_packager_rejects_a_different_context_source_lock(package_context):
    package, lock, _ = package_context
    module = verifier()
    assert hasattr(module, "prepare_package"), "the actual Docker context needs an enforced verification entry"
    wrong = dict(lock, source_commit="0" * 40)
    (package / "php-source.json").write_text(json.dumps(wrong))
    with pytest.raises(ValueError, match="package_source_lock_mismatch"):
        module.prepare_package(package, lock)


def test_packager_never_follows_an_output_checksum_symlink(package_context):
    package, lock, _ = package_context
    outside = package.parent / "outside"
    outside.write_bytes(b"must stay unchanged")
    path = package / "php-runtime/artifact.sha256"
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="package_output_invalid"):
        verifier().prepare_package(package, lock)
    assert outside.read_bytes() == b"must stay unchanged"


def test_packager_refuses_floating_or_overridable_from(package_context):
    package, lock, _ = package_context
    module = verifier()
    assert hasattr(module, "prepare_package"), "the actual Docker context needs an enforced verification entry"
    (package / "Dockerfile.hardened").write_text("ARG BASE=zentao:latest\nFROM ${BASE}\n")
    with pytest.raises(ValueError, match="package_base_digest_mismatch"):
        module.prepare_package(package, lock)


def test_only_verified_packaging_entry_invokes_docker_with_fixed_inputs(package_context):
    package, lock, _ = package_context
    module = verifier()
    assert hasattr(module, "build_package"), "building must use the verification entry"
    calls = []
    module.build_package(package, lock, run=lambda args, **kwargs: calls.append((args, kwargs)))
    args, options = calls[0]
    assert args[:4] == ["docker", "build", "--network", "none"]
    assert "--build-arg" not in args and args[-1] == str(package)
    assert options["check"] is True
    assert (package / "package-receipt.json").is_file()


def apache_observer():
    path = SDK / "run_php_apache_probe.py"
    assert path.is_file(), "hard OOM requires positive wait status and target identity evidence"
    spec = importlib.util.spec_from_file_location("sdk_php_observer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oom_observer_requires_exit_one_for_the_exact_worker():
    trace = "wait4(-1, [{WIFEXITED(s) && WEXITSTATUS(s) == 1}], WNOHANG, NULL) = 9\n"
    assert apache_observer().parse_wait_exit(trace, 9) == {"exit_code": 1, "signal": 0, "core": False}
    with pytest.raises(ValueError, match="oom_exit_unproven"):
        apache_observer().parse_wait_exit("wait4(-1, 0xabc, WNOHANG, NULL) = 0\n", 10)


@pytest.mark.parametrize("status", ["WIFEXITED(s) && WEXITSTATUS(s) == 0",
                                  "WIFSIGNALED(s) && WTERMSIG(s) == SIGSEGV && WCOREDUMP(s)",
                                  "WIFSIGNALED(s) && WTERMSIG(s) == SIGBUS"])
def test_oom_observer_cannot_accept_a_crash_or_different_exit_status(status):
    trace = f"wait4(-1, [{{{status}}}], WNOHANG, NULL) = 9\n"
    with pytest.raises(ValueError, match="oom_exit_rejected"):
        apache_observer().parse_wait_exit(trace, 9)


def test_observer_refuses_attach_outside_the_attempt_cgroup_or_namespace():
    module = apache_observer()
    cid = "a" * 64
    init = {"pid": 100, "pidns": "pid:[200]", "cgroup": "0::/docker/" + cid}
    parent = {"pid": 101, "ppid": 100, "pidns": "pid:[200]", "cgroup": init["cgroup"], "exe": "/opt/zbox/run/apache/httpd"}
    module.validate_httpd_identity(init, parent, cid, "pid:[1]")
    with pytest.raises(ValueError, match="apache_identity_invalid"):
        module.validate_httpd_identity(init, dict(parent, cgroup="0::/system.slice/production.service"), cid, "pid:[1]")
    with pytest.raises(ValueError, match="apache_identity_invalid"):
        module.validate_httpd_identity(init, dict(parent, pidns="pid:[1]"), cid, "pid:[1]")


def test_oom_observer_stops_on_another_child_exit():
    trace = "wait4(-1, [{WIFEXITED(s) && WEXITSTATUS(s) == 1}], WNOHANG, NULL) = 12\n"
    with pytest.raises(ValueError, match="unexpected_child_exit"):
        apache_observer().parse_wait_exit(trace, 9)


def test_apache_final_start_rechecks_after_vendor_configuration_rendering():
    dockerfile = (SDK / "Dockerfile.hardened").read_text()
    assert "f3f46226116154e84199023e17c471718dbc886d0630267591e5dd3ae75d4c56" in dockerfile
    assert "^exec /opt/zbox/bin/apachectl -D FOREGROUND$" in dockerfile
    assert "i /usr/local/bin/verify-and-start.sh --verify-only" in dockerfile


def test_entrypoint_acceptance_requires_all_live_php_mappings():
    path = SDK / "run_php_entrypoint_probe.py"
    assert path.is_file(), "the vendor startup path needs live PHP mapping evidence"
    spec = importlib.util.spec_from_file_location("sdk_php_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="live_php_mapping_missing"):
        module.check_runtime_maps("000-fff r-xp /opt/zbox/run/apache/modules/libphp.so\n")
    module.check_runtime_maps("\n".join("000-fff r-xp " + name for name in (
        "/opt/zbox/run/apache/modules/libphp.so", "/opt/zbox/run/php/extensions/opcache.so",
        "/opt/zbox/run/php/extensions/ioncube_loader_lin_8.1.so")))


def test_runtime_maps_allow_deleted_anonymous_memory_but_reject_deleted_php():
    path = SDK / "run_php_entrypoint_probe.py"
    spec = importlib.util.spec_from_file_location("sdk_php_entrypoint_maps", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    valid = "\n".join("000-fff r-xp " + name for name in (
        "/opt/zbox/run/apache/modules/libphp.so", "/opt/zbox/run/php/extensions/opcache.so",
        "/opt/zbox/run/php/extensions/ioncube_loader_lin_8.1.so"))
    module.check_runtime_maps(valid + "\n000-fff rw-s /dev/zero (deleted)\n")
    with pytest.raises(ValueError, match="live_php_mapping_deleted"):
        module.check_runtime_maps(valid + "\n000-fff r-xp /opt/zbox/run/apache/modules/libphp.so (deleted)\n")


def test_probe_observer_preserves_stderr_failure_evidence(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, "stage\n", "fatal evidence\n"))
    assert "fatal evidence" in apache_observer()._logs("isolated-id")


def test_entrypoint_readiness_allows_created_state_before_start_finishes():
    path = SDK / "run_php_entrypoint_probe.py"
    spec = importlib.util.spec_from_file_location("sdk_php_entrypoint_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "startup_running"), "Docker start is asynchronous to the readiness consumer"
    assert module.startup_running({"Status": "created", "Running": False}) is False
    assert module.startup_running({"Status": "running", "Running": True}) is True
    with pytest.raises(RuntimeError, match="vendor_startup_exited"):
        module.startup_running({"Status": "exited", "Running": False})
