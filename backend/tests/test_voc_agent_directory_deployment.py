from pathlib import Path


ACCEPT = Path(__file__).parents[2] / "deploy" / "cloud" / "accept.sh"


def test_authenticated_member_acceptance_requires_voc_catalog_access() -> None:
    script = ACCEPT.read_text(encoding="utf-8")

    assert '"voc" not in agents' in script
    assert '"marketing-gtm-bot" in agents' in script
