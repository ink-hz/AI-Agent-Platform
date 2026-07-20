import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .cluster import routes as cluster_routes
from .cluster.monitor import ClusterMonitor, cluster_poll_loop
from .config import load_config
from .health import routes as health_routes
from .health.poller import HealthCache, poll_loop
from .registry import routes as registry_routes
from .registry.repository import YamlRepository


async def cancel_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app(
    registry_path: str | None = None,
    cluster_contract_path: str | None = None,
    *,
    start_poller: bool = True,
) -> FastAPI:
    config = load_config()
    path = registry_path or config.registry_path
    repo = YamlRepository(path)
    agents = repo.list_agents()
    cache = HealthCache([agent.id for agent in agents])
    cluster_monitor = ClusterMonitor(
        cluster_contract_path or config.metabot_contract_path,
        timeout=config.probe_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        if start_poller:
            tasks = [
                asyncio.create_task(
                    poll_loop(
                        cache,
                        agents,
                        config.poll_interval_seconds,
                        config.probe_timeout_seconds,
                    )
                ),
                asyncio.create_task(
                    cluster_poll_loop(
                        cluster_monitor,
                        config.cluster_poll_interval_seconds,
                    )
                ),
            ]
        try:
            yield
        finally:
            await cancel_tasks(tasks)

    app = FastAPI(title="Orbbec AI Agent Platform", version="0.1.0", lifespan=lifespan)
    app.state.repo = repo
    app.state.health_cache = cache
    app.state.cluster_monitor = cluster_monitor

    @app.get("/api/health")
    def platform_health() -> dict:
        return {"status": "ok"}

    app.include_router(health_routes.router)
    app.include_router(registry_routes.router)
    app.include_router(cluster_routes.router)

    if os.path.isdir(config.static_dir):
        app.mount("/", StaticFiles(directory=config.static_dir, html=True), name="portal")

    return app


app = create_app() if os.getenv("PLATFORM_EAGER_APP") else None
