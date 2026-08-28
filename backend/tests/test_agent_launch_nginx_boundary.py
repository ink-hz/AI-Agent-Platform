from pathlib import Path

NGINX = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "cloud"
    / "agent-domain.nginx.conf"
)


def test_agent_identity_backchannel_is_hidden_before_the_platform_proxy() -> None:
    value = NGINX.read_text(encoding="utf-8")
    exchange = "location = /api/v1/internal/agent-launch/exchange"
    binding = "location ~ ^/api/v1/internal/agent-bindings/"
    fallback = "location / {"

    assert exchange in value
    assert binding in value
    assert value.index(exchange) < value.rindex(fallback)
    assert value.index(binding) < value.rindex(fallback)
    exchange_block = value[value.index(exchange) : value.index("\n    }", value.index(exchange))]
    binding_block = value[value.index(binding) : value.index("\n    }", value.index(binding))]
    assert "return 404;" in exchange_block
    assert "return 404;" in binding_block
    assert "/office/" in value
