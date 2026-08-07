from pathlib import Path


def test_http_client_dependency_is_installable_and_unique() -> None:
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8").splitlines()

    http_clients = [line for line in requirements if line.startswith("httpx")]
    assert http_clients == ["httpx>=0.27"]
