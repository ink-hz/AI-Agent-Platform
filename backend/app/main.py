import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .health import routes as health_routes
from .health.poller import HealthCache, poll_loop
from .registry import routes as registry_routes
from .registry.repository import YamlRepository


def create_app(registry_path: str | None = None, *, start_poller: bool = True) -> FastAPI:
    config = load_config()
    path = registry_path or config.registry_path
    repo = YamlRepository(path)
    agents = repo.list_agents()
    cache = HealthCache([agent.id for agent in agents])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if start_poller:
            task = asyncio.create_task(
                poll_loop(
                    cache,
                    agents,
                    config.poll_interval_seconds,
                    config.probe_timeout_seconds,
                )
            )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(title="Orbbec AI Agent Platform", version="0.1.0", lifespan=lifespan)
    app.state.repo = repo
    app.state.health_cache = cache

    @app.get("/api/health")
    def platform_health() -> dict:
        return {"status": "ok"}

    app.include_router(health_routes.router)
    app.include_router(registry_routes.router)

    if os.path.isdir(config.static_dir):
        app.mount("/", StaticFiles(directory=config.static_dir, html=True), name="portal")

    return app


app = create_app() if os.getenv("PLATFORM_EAGER_APP") else None
