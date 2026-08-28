from pathlib import Path


def test_http_client_dependency_is_installable_and_unique() -> None:
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8").splitlines()

    http_clients = [line for line in requirements if line.startswith("httpx")]
    assert http_clients == ["httpx>=0.27"]


def test_dingtalk_stream_dependency_has_one_stable_pin_in_each_runtime() -> None:
    backend = Path(__file__).resolve().parents[1]

    for filename in ("requirements.txt", "requirements.cloud.txt"):
        requirements = (backend / filename).read_text(encoding="utf-8").splitlines()
        dingtalk_dependencies = [
            line for line in requirements if line.strip().lower().startswith("dingtalk")
        ]
        assert dingtalk_dependencies == ["dingtalk-stream==0.24.3"]


def test_jcs_dependency_has_one_stable_pin_in_each_runtime() -> None:
    backend = Path(__file__).resolve().parents[1]

    for filename in ("requirements.txt", "requirements.cloud.txt"):
        requirements = (backend / filename).read_text(encoding="utf-8").splitlines()
        jcs_dependencies = [
            line for line in requirements if line.strip().lower().startswith("jcs")
        ]
        assert jcs_dependencies == ["jcs==0.2.1"]
