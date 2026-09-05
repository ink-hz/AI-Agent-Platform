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


def test_attachment_image_decoder_has_one_stable_pin_in_each_runtime() -> None:
    backend = Path(__file__).resolve().parents[1]

    for filename in ("requirements.txt", "requirements.cloud.txt"):
        requirements = (backend / filename).read_text(encoding="utf-8").splitlines()
        pillow_dependencies = [
            line for line in requirements if line.strip().lower().startswith("pillow")
        ]
        assert pillow_dependencies == ["Pillow==11.3.0"]


def test_attachment_structural_parsers_have_stable_runtime_pins() -> None:
    backend = Path(__file__).resolve().parents[1]

    for filename in ("requirements.txt", "requirements.cloud.txt"):
        requirements = (backend / filename).read_text(encoding="utf-8").splitlines()
        assert [line for line in requirements if line.startswith("pypdf")] == [
            "pypdf==6.16.2"
        ]
        assert [line for line in requirements if line.startswith("defusedxml")] == [
            "defusedxml==0.7.1"
        ]


def test_hr_report_exporters_have_stable_runtime_pins() -> None:
    backend = Path(__file__).resolve().parents[1]

    for filename in ("requirements.txt", "requirements.cloud.txt"):
        requirements = (backend / filename).read_text(encoding="utf-8").splitlines()
        assert [line for line in requirements if line.startswith("openpyxl")] == [
            "openpyxl==3.1.5"
        ]
        assert [line for line in requirements if line.startswith("reportlab")] == [
            "reportlab==4.4.10"
        ]


def test_contract_schema_validator_is_pinned_for_tests_only() -> None:
    """The FAE identity contract needs a real draft 2020-12 validator.

    It must arrive through requirements.txt -- never as a package that only
    happens to be installed on a developer machine -- and it must not be
    dragged into the cloud runtime, which serves the contract without
    validating it. The pin has to match the contract package's own pin, or the
    two repositories could disagree about what the schema means.
    """
    backend = Path(__file__).resolve().parents[1]
    pin = "jsonschema==4.26.0"

    requirements = (backend / "requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    cloud = (backend / "requirements.cloud.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    contract = (
        backend.parent / "contracts" / "fae_identity_v1" / "pyproject.toml"
    ).read_text(encoding="utf-8")

    assert [
        line for line in requirements if line.strip().lower().startswith("jsonschema")
    ] == [pin]
    assert [
        line for line in cloud if line.strip().lower().startswith("jsonschema")
    ] == []
    assert f'"{pin}"' in contract
