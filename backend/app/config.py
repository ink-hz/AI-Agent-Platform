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
    flywheel_enabled: bool
    flywheel_database_url: str | None
    flywheel_keychain_service: str
    flywheel_keychain_account: str
    usage_cache_seconds: float
    active_window_minutes: int


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
        flywheel_enabled=os.getenv("PLATFORM_FLYWHEEL_ENABLED", "1") not in {
            "0",
            "false",
            "False",
        },
        flywheel_database_url=os.getenv("PLATFORM_FLYWHEEL_DATABASE_URL"),
        flywheel_keychain_service=os.getenv(
            "PLATFORM_FLYWHEEL_KEYCHAIN_SERVICE",
            "flywheel-analyst-database-url",
        ),
        flywheel_keychain_account=os.getenv(
            "PLATFORM_FLYWHEEL_KEYCHAIN_ACCOUNT",
            "neo",
        ),
        usage_cache_seconds=float(os.getenv("PLATFORM_USAGE_CACHE_SECONDS", "60")),
        active_window_minutes=int(os.getenv("PLATFORM_ACTIVE_WINDOW_MINUTES", "15")),
    )
