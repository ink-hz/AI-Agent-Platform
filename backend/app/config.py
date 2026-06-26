import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    registry_path: str
    poll_interval_seconds: float
    probe_timeout_seconds: float
    static_dir: str
    host: str
    port: int


def load_config() -> Config:
    return Config(
        registry_path=os.getenv("PLATFORM_REGISTRY_PATH", "../registry.yaml"),
        poll_interval_seconds=float(os.getenv("PLATFORM_POLL_INTERVAL", "30")),
        probe_timeout_seconds=float(os.getenv("PLATFORM_PROBE_TIMEOUT", "3")),
        static_dir=os.getenv("PLATFORM_STATIC_DIR", "app/static"),
        host=os.getenv("PLATFORM_HOST", "0.0.0.0"),
        port=int(os.getenv("PLATFORM_PORT", "80")),
    )
