import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    registry_path: str
    metabot_contract_path: str
    poll_interval_seconds: float
    cluster_poll_interval_seconds: float
    probe_timeout_seconds: float
    static_dir: str
    host: str
    port: int


def load_config() -> Config:
    return Config(
        registry_path=os.getenv("PLATFORM_REGISTRY_PATH", "../registry.yaml"),
        metabot_contract_path=os.getenv(
            "PLATFORM_METABOT_CONTRACT_PATH",
            "/Users/neo/Developer/work/Orbbec-Agent-Team/deploy/metabot.runtime-contract.json",
        ),
        poll_interval_seconds=float(os.getenv("PLATFORM_POLL_INTERVAL", "30")),
        cluster_poll_interval_seconds=float(
            os.getenv("PLATFORM_CLUSTER_POLL_INTERVAL", "10")
        ),
        probe_timeout_seconds=float(os.getenv("PLATFORM_PROBE_TIMEOUT", "3")),
        static_dir=os.getenv("PLATFORM_STATIC_DIR", "app/static"),
        host=os.getenv("PLATFORM_HOST", "0.0.0.0"),
        port=int(os.getenv("PLATFORM_PORT", "80")),
    )
