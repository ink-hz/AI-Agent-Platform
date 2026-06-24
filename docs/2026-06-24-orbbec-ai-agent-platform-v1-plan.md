# Orbbec AI Agent Platform v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone "platform facade" — a FastAPI service + React/Vite Portal that registers independent FAE/ADMIN agents from a `registry.yaml`, shows their health, and links users out to each agent.

**Architecture:** Federation model. `platform-api` (FastAPI, single process on port 80) loads `registry.yaml`, serves `/api/*` JSON and the Portal static build at `/`. A background asyncio poller probes each agent's `/health`, normalizes the heterogeneous responses, and caches results. The Portal renders agent cards with live status; clicking a card opens the agent's external `entry_url`. The two agents are never modified.

**Tech Stack:** Python 3.11+, FastAPI, pydantic v2, httpx, PyYAML, pytest + respx; frontend React 19 + Vite + TypeScript + vitest.

**Spec:** `docs/2026-06-24-orbbec-ai-agent-platform-v1-design.md`

**Conventions for every task:** run backend tests from the `backend/` directory (`cd backend && pytest ...`). The repo already has `git init`'d with the design doc committed.

---

### Task 1: Backend scaffolding, registry.yaml, config

**Files:**
- Create: `registry.yaml`
- Create: `backend/requirements.txt`
- Create: `backend/pytest.ini`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/app/registry/__init__.py`
- Create: `backend/app/health/__init__.py`

- [ ] **Step 1: Create `registry.yaml`** (root of repo)

```yaml
version: 1
agents:
  - id: fae
    name: "AI FAE Agent"
    domain: "技术支持"
    description: "FAE 技术问答 / 产品规格 / SDK / 排障 / 经验库"
    icon: "🛠️"
    owner: "<负责人>"
    env: "prod"
    status: "active"
    entry_url: "http://<fae-host>/app/"
    health:
      url: "http://<fae-host>/health"
      type: "fae"
    api_base: "http://<fae-host>"
    version: ""
    tags: ["pg-flywheel", "review-center"]

  - id: admin
    name: "AI ADMIN Agent"
    domain: "行政"
    description: "行政制度 / 办公流程 / 会议室 / 差旅 / 办公用品 / 停车 / 快递"
    icon: "🏢"
    owner: "<负责人>"
    env: "prod"
    status: "active"
    entry_url: "http://<admin-host>/app/"
    health:
      url: "http://<admin-host>/health"
      type: "admin"
    api_base: "http://<admin-host>"
    version: ""
    tags: ["in-memory-session"]
```

- [ ] **Step 2: Create `backend/requirements.txt`**

```
fastapi>=0.115
uvicorn[standard]>=0.32
httpx>=0.27
pyyaml>=6.0
pydantic>=2.7
pytest>=8.0
respx>=0.21
```

- [ ] **Step 3: Create `backend/pytest.ini`**

```ini
[pytest]
pythonpath = .
testpaths = tests
asyncio_mode = auto
```

- [ ] **Step 4: Create empty package markers**

Create `backend/app/__init__.py`, `backend/app/registry/__init__.py`, `backend/app/health/__init__.py` — each an empty file.

- [ ] **Step 5: Create `backend/app/config.py`**

```python
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
```

- [ ] **Step 6: Install deps and verify**

Run: `cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
Expected: installs without error.

- [ ] **Step 7: Commit**

```bash
git add registry.yaml backend/requirements.txt backend/pytest.ini backend/app/__init__.py backend/app/config.py backend/app/registry/__init__.py backend/app/health/__init__.py
git commit -m "chore: scaffold platform backend, registry.yaml, config"
```

---

### Task 2: Registry models (AgentEntry)

**Files:**
- Create: `backend/app/registry/models.py`
- Test: `backend/tests/test_registry_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_registry_models.py
from app.registry.models import AgentEntry, Registry


def _entry_kwargs(**over):
    base = dict(
        id="fae",
        name="AI FAE Agent",
        domain="技术支持",
        description="desc",
        icon="🛠️",
        owner="someone",
        entry_url="http://host/app/",
        health={"url": "http://host/health", "type": "fae"},
    )
    base.update(over)
    return base


def test_agent_entry_defaults():
    e = AgentEntry(**_entry_kwargs())
    assert e.env == "prod"
    assert e.status == "active"
    assert e.version == ""
    assert e.tags == []
    assert e.health.type == "fae"


def test_public_dict_hides_health_url_and_api_base():
    e = AgentEntry(**_entry_kwargs(api_base="http://host"))
    pub = e.public_dict()
    assert pub["id"] == "fae"
    assert pub["entry_url"] == "http://host/app/"
    assert "health" not in pub
    assert "api_base" not in pub


def test_registry_parses_agent_list():
    reg = Registry.model_validate(
        {"version": 1, "agents": [_entry_kwargs()]}
    )
    assert len(reg.agents) == 1
    assert reg.agents[0].id == "fae"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_registry_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.registry.models'`

- [ ] **Step 3: Write `backend/app/registry/models.py`**

```python
from pydantic import BaseModel, Field


class HealthSpec(BaseModel):
    url: str
    type: str = "generic"


class AgentEntry(BaseModel):
    id: str
    name: str
    domain: str = ""
    description: str = ""
    icon: str = ""
    owner: str = ""
    env: str = "prod"
    status: str = "active"  # active | maintenance | offline
    entry_url: str
    health: HealthSpec
    api_base: str | None = None
    version: str = ""
    tags: list[str] = Field(default_factory=list)

    def public_dict(self) -> dict:
        """Outward-facing projection: never leak internal health.url / api_base."""
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            "description": self.description,
            "icon": self.icon,
            "owner": self.owner,
            "env": self.env,
            "status": self.status,
            "entry_url": self.entry_url,
            "version": self.version,
            "tags": self.tags,
        }


class Registry(BaseModel):
    version: int = 1
    agents: list[AgentEntry]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_registry_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/registry/models.py backend/tests/test_registry_models.py
git commit -m "feat: add registry AgentEntry/Registry models with public projection"
```

---

### Task 3: Registry repository (YAML loader, fail-fast validation)

**Files:**
- Create: `backend/app/registry/repository.py`
- Test: `backend/tests/test_registry_repository.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_registry_repository.py
import textwrap
import pytest
from app.registry.repository import YamlRepository, RegistryError


GOOD = textwrap.dedent(
    """
    version: 1
    agents:
      - id: fae
        name: "AI FAE Agent"
        entry_url: "http://fae/app/"
        health: {url: "http://fae/health", type: "fae"}
      - id: admin
        name: "AI ADMIN Agent"
        entry_url: "http://admin/app/"
        health: {url: "http://admin/health", type: "admin"}
    """
)


def _write(tmp_path, content):
    p = tmp_path / "registry.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_loads_and_lists_agents(tmp_path):
    repo = YamlRepository(_write(tmp_path, GOOD))
    agents = repo.list_agents()
    assert [a.id for a in agents] == ["fae", "admin"]


def test_get_agent_by_id(tmp_path):
    repo = YamlRepository(_write(tmp_path, GOOD))
    assert repo.get_agent("admin").name == "AI ADMIN Agent"
    assert repo.get_agent("missing") is None


def test_missing_file_fails_fast(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        YamlRepository(str(tmp_path / "nope.yaml"))


def test_invalid_yaml_fails_fast(tmp_path):
    with pytest.raises(RegistryError, match="invalid YAML"):
        YamlRepository(_write(tmp_path, "version: 1\nagents: [oops: ]["))


def test_missing_required_field_fails_fast(tmp_path):
    bad = textwrap.dedent(
        """
        version: 1
        agents:
          - id: fae
            name: "no entry_url / health"
        """
    )
    with pytest.raises(RegistryError, match="validation failed"):
        YamlRepository(_write(tmp_path, bad))


def test_duplicate_ids_fail_fast(tmp_path):
    dup = textwrap.dedent(
        """
        version: 1
        agents:
          - id: fae
            name: a
            entry_url: "http://a/app/"
            health: {url: "http://a/health"}
          - id: fae
            name: b
            entry_url: "http://b/app/"
            health: {url: "http://b/health"}
        """
    )
    with pytest.raises(RegistryError, match="duplicate"):
        YamlRepository(_write(tmp_path, dup))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_registry_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.registry.repository'`

- [ ] **Step 3: Write `backend/app/registry/repository.py`**

```python
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import ValidationError

from .models import AgentEntry, Registry


class RegistryError(Exception):
    """Raised on any registry load/validation failure (fail fast at startup)."""


class Repository(Protocol):
    def list_agents(self) -> list[AgentEntry]: ...
    def get_agent(self, agent_id: str) -> AgentEntry | None: ...


class YamlRepository:
    def __init__(self, path: str) -> None:
        self._registry = self._load(Path(path))

    @staticmethod
    def _load(path: Path) -> Registry:
        if not path.exists():
            raise RegistryError(f"registry file not found: {path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RegistryError(f"invalid YAML in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RegistryError(
                f"registry must be a mapping, got {type(data).__name__}"
            )
        try:
            registry = Registry.model_validate(data)
        except ValidationError as exc:
            raise RegistryError(f"registry validation failed: {exc}") from exc

        ids = [a.id for a in registry.agents]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise RegistryError(f"duplicate agent ids: {sorted(dupes)}")
        return registry

    def list_agents(self) -> list[AgentEntry]:
        return list(self._registry.agents)

    def get_agent(self, agent_id: str) -> AgentEntry | None:
        for agent in self._registry.agents:
            if agent.id == agent_id:
                return agent
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_registry_repository.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/registry/repository.py backend/tests/test_registry_repository.py
git commit -m "feat: add YamlRepository with fail-fast validation and dup-id check"
```

---

### Task 4: Health normalizer

**Files:**
- Create: `backend/app/health/normalizer.py`
- Test: `backend/tests/test_health_normalizer.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_health_normalizer.py
from app.health.normalizer import normalize


def test_fae_metrics():
    raw = {"status": "ok", "qa_indexed": True, "products_loaded": 42}
    metrics = normalize("fae", raw)
    assert {"label": "QA 索引", "value": "已加载"} in metrics
    assert {"label": "产品数", "value": 42} in metrics


def test_admin_metrics():
    raw = {"status": "ok", "llm_model": "glm-5.2", "chunks_loaded": 12345,
           "documents_loaded": 42}
    metrics = normalize("admin", raw)
    labels = {m["label"]: m["value"] for m in metrics}
    assert labels["模型"] == "glm-5.2"
    assert labels["知识块"] == 12345
    assert labels["文档数"] == 42


def test_unknown_type_falls_back_to_generic_empty():
    assert normalize("sales", {"anything": 1}) == []


def test_normalizer_never_raises_on_bad_shape():
    assert normalize("fae", {"qa_indexed": None}) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_health_normalizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.health.normalizer'`

- [ ] **Step 3: Write `backend/app/health/normalizer.py`**

```python
"""Map heterogeneous agent /health payloads into a uniform metrics list."""


def _fae(raw: dict) -> list[dict]:
    metrics: list[dict] = []
    if "qa_indexed" in raw:
        metrics.append(
            {"label": "QA 索引", "value": "已加载" if raw["qa_indexed"] else "未加载"}
        )
    if "products_loaded" in raw:
        metrics.append({"label": "产品数", "value": raw["products_loaded"]})
    return metrics


def _admin(raw: dict) -> list[dict]:
    metrics: list[dict] = []
    if raw.get("llm_model"):
        metrics.append({"label": "模型", "value": raw["llm_model"]})
    if "chunks_loaded" in raw:
        metrics.append({"label": "知识块", "value": raw["chunks_loaded"]})
    if "documents_loaded" in raw:
        metrics.append({"label": "文档数", "value": raw["documents_loaded"]})
    return metrics


def _generic(raw: dict) -> list[dict]:
    return []


_NORMALIZERS = {"fae": _fae, "admin": _admin, "generic": _generic}


def normalize(health_type: str, raw: dict) -> list[dict]:
    fn = _NORMALIZERS.get(health_type, _generic)
    try:
        return fn(raw)
    except Exception:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_health_normalizer.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/health/normalizer.py backend/tests/test_health_normalizer.py
git commit -m "feat: add per-type health normalizer (fae/admin/generic)"
```

---

### Task 5: Health poller and cache

**Files:**
- Create: `backend/app/health/poller.py`
- Test: `backend/tests/test_health_poller.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_health_poller.py
import httpx
import respx
import pytest

from app.registry.models import AgentEntry
from app.health.poller import HealthCache, HealthStatus, probe_agent, poll_once


def _agent(aid, url, htype):
    return AgentEntry(
        id=aid, name=aid, entry_url=f"http://{aid}/app/",
        health={"url": url, "type": htype},
    )


def test_cache_starts_unknown():
    cache = HealthCache(["fae", "admin"])
    assert cache.get("fae").online is None
    assert {s.id for s in cache.all()} == {"fae", "admin"}


@pytest.mark.asyncio
@respx.mock
async def test_probe_online_normalizes_metrics():
    respx.get("http://fae/health").mock(
        return_value=httpx.Response(200, json={"qa_indexed": True, "products_loaded": 7})
    )
    async with httpx.AsyncClient() as client:
        status = await probe_agent(client, _agent("fae", "http://fae/health", "fae"), 3.0)
    assert status.online is True
    assert status.latency_ms is not None
    assert {"label": "产品数", "value": 7} in status.metrics
    assert status.raw == {"qa_indexed": True, "products_loaded": 7}


@pytest.mark.asyncio
@respx.mock
async def test_probe_offline_on_error():
    respx.get("http://admin/health").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as client:
        status = await probe_agent(client, _agent("admin", "http://admin/health", "admin"), 3.0)
    assert status.online is False
    assert status.metrics == []


@pytest.mark.asyncio
@respx.mock
async def test_probe_offline_on_non_200():
    respx.get("http://fae/health").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        status = await probe_agent(client, _agent("fae", "http://fae/health", "fae"), 3.0)
    assert status.online is False


@pytest.mark.asyncio
@respx.mock
async def test_poll_once_updates_all():
    respx.get("http://fae/health").mock(return_value=httpx.Response(200, json={"products_loaded": 1}))
    respx.get("http://admin/health").mock(return_value=httpx.Response(200, json={"chunks_loaded": 2}))
    agents = [_agent("fae", "http://fae/health", "fae"),
              _agent("admin", "http://admin/health", "admin")]
    cache = HealthCache([a.id for a in agents])
    async with httpx.AsyncClient() as client:
        await poll_once(cache, agents, client, 3.0)
    assert cache.get("fae").online is True
    assert cache.get("admin").online is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_health_poller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.health.poller'`

- [ ] **Step 3: Write `backend/app/health/poller.py`**

```python
import asyncio
import time
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field

from ..registry.models import AgentEntry
from .normalizer import normalize


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HealthStatus(BaseModel):
    id: str
    online: bool | None = None
    checked_at: str | None = None
    latency_ms: int | None = None
    version: str | None = None
    metrics: list[dict] = Field(default_factory=list)
    raw: dict | None = None


class HealthCache:
    def __init__(self, agent_ids: list[str]) -> None:
        self._data: dict[str, HealthStatus] = {
            aid: HealthStatus(id=aid) for aid in agent_ids
        }

    def get(self, agent_id: str) -> HealthStatus | None:
        return self._data.get(agent_id)

    def all(self) -> list[HealthStatus]:
        return list(self._data.values())

    def set(self, status: HealthStatus) -> None:
        self._data[status.id] = status


async def probe_agent(
    client: httpx.AsyncClient, agent: AgentEntry, timeout: float
) -> HealthStatus:
    start = time.perf_counter()
    try:
        resp = await client.get(agent.health.url, timeout=timeout)
    except Exception:
        return HealthStatus(id=agent.id, online=False, checked_at=_now_iso())
    latency_ms = int((time.perf_counter() - start) * 1000)
    if resp.status_code != 200:
        return HealthStatus(
            id=agent.id, online=False, checked_at=_now_iso(), latency_ms=latency_ms
        )
    try:
        raw = resp.json()
    except Exception:
        raw = {}
    return HealthStatus(
        id=agent.id,
        online=True,
        checked_at=_now_iso(),
        latency_ms=latency_ms,
        version=agent.version or None,
        metrics=normalize(agent.health.type, raw if isinstance(raw, dict) else {}),
        raw=raw if isinstance(raw, dict) else None,
    )


async def poll_once(
    cache: HealthCache,
    agents: list[AgentEntry],
    client: httpx.AsyncClient,
    timeout: float,
) -> None:
    results = await asyncio.gather(
        *(probe_agent(client, a, timeout) for a in agents)
    )
    for status in results:
        cache.set(status)


async def poll_loop(
    cache: HealthCache,
    agents: list[AgentEntry],
    interval: float,
    timeout: float,
) -> None:
    async with httpx.AsyncClient() as client:
        while True:
            await poll_once(cache, agents, client, timeout)
            await asyncio.sleep(interval)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_health_poller.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/health/poller.py backend/tests/test_health_poller.py
git commit -m "feat: add health poller, cache, and per-agent probe"
```

---

### Task 6: API routes + app factory + static serving

**Files:**
- Create: `backend/app/registry/routes.py`
- Create: `backend/app/health/routes.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/test_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_api.py
import textwrap
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


REG = textwrap.dedent(
    """
    version: 1
    agents:
      - id: fae
        name: "AI FAE Agent"
        domain: "技术支持"
        entry_url: "http://fae/app/"
        health: {url: "http://fae/health", type: "fae"}
        api_base: "http://fae"
      - id: admin
        name: "AI ADMIN Agent"
        domain: "行政"
        entry_url: "http://admin/app/"
        health: {url: "http://admin/health", type: "admin"}
    """
)


@pytest.fixture()
def client(tmp_path):
    p = tmp_path / "registry.yaml"
    p.write_text(REG, encoding="utf-8")
    app = create_app(registry_path=str(p), start_poller=False)
    return TestClient(app)


def test_platform_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_agents_hides_internal_fields(client):
    r = client.get("/api/agents")
    assert r.status_code == 200
    agents = r.json()
    assert [a["id"] for a in agents] == ["fae", "admin"]
    assert "health" not in agents[0]
    assert "api_base" not in agents[0]
    assert agents[0]["entry_url"] == "http://fae/app/"


def test_get_single_agent_and_404(client):
    assert client.get("/api/agents/fae").json()["name"] == "AI FAE Agent"
    assert client.get("/api/agents/missing").status_code == 404


def test_batch_health_returns_list_not_single_agent(client):
    r = client.get("/api/agents/health")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    ids = {s["id"] for s in body}
    assert ids == {"fae", "admin"}
    # poller disabled -> unknown status
    assert body[0]["online"] is None


def test_per_agent_health_and_404(client):
    assert client.get("/api/agents/fae/health").json()["id"] == "fae"
    assert client.get("/api/agents/missing/health").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write `backend/app/registry/routes.py`**

```python
from fastapi import APIRouter, HTTPException, Request

from .repository import Repository

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _repo(request: Request) -> Repository:
    return request.app.state.repo


@router.get("")
def list_agents(request: Request) -> list[dict]:
    return [a.public_dict() for a in _repo(request).list_agents()]


@router.get("/{agent_id}")
def get_agent(agent_id: str, request: Request) -> dict:
    agent = _repo(request).get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    return agent.public_dict()
```

- [ ] **Step 4: Write `backend/app/health/routes.py`**

```python
from fastapi import APIRouter, HTTPException, Request

from .poller import HealthCache

# NOTE: this router MUST be included before registry.routes so that the static
# path /api/agents/health matches here instead of registry's /api/agents/{id}.
router = APIRouter(prefix="/api/agents", tags=["health"])


def _cache(request: Request) -> HealthCache:
    return request.app.state.health_cache


@router.get("/health")
def batch_health(request: Request) -> list[dict]:
    return [s.model_dump() for s in _cache(request).all()]


@router.get("/{agent_id}/health")
def agent_health(agent_id: str, request: Request) -> dict:
    status = _cache(request).get(agent_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    return status.model_dump()
```

- [ ] **Step 5: Write `backend/app/main.py`**

```python
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .health.poller import HealthCache, poll_loop
from .health import routes as health_routes
from .registry import routes as registry_routes
from .registry.repository import YamlRepository


def create_app(registry_path: str | None = None, *, start_poller: bool = True) -> FastAPI:
    config = load_config()
    path = registry_path or config.registry_path
    repo = YamlRepository(path)
    agents = repo.list_agents()
    cache = HealthCache([a.id for a in agents])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if start_poller:
            task = asyncio.create_task(
                poll_loop(cache, agents, config.poll_interval_seconds,
                          config.probe_timeout_seconds)
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

    # health router first so /api/agents/health resolves before /api/agents/{id}
    app.include_router(health_routes.router)
    app.include_router(registry_routes.router)

    if os.path.isdir(config.static_dir):
        app.mount("/", StaticFiles(directory=config.static_dir, html=True), name="portal")

    return app


app = create_app() if os.getenv("PLATFORM_EAGER_APP") else None
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && pytest tests/test_api.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS (all tasks' tests green)

- [ ] **Step 8: Commit**

```bash
git add backend/app/registry/routes.py backend/app/health/routes.py backend/app/main.py backend/tests/test_api.py
git commit -m "feat: add registry+health API routes, app factory, static mount"
```

---

### Task 7: Frontend Portal (React/Vite)

**Files:**
- Create: `webui/package.json`
- Create: `webui/vite.config.ts`
- Create: `webui/tsconfig.json`
- Create: `webui/index.html`
- Create: `webui/src/main.tsx`
- Create: `webui/src/types.ts`
- Create: `webui/src/api.ts`
- Create: `webui/src/status.ts`
- Create: `webui/src/App.tsx`
- Create: `webui/src/styles.css`
- Test: `webui/src/status.test.ts`

- [ ] **Step 1: Create `webui/package.json`**

```json
{
  "name": "orbbec-agent-platform-webui",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.6.0",
    "vite": "^7.0.0",
    "vitest": "^2.1.0"
  }
}
```

- [ ] **Step 2: Create `webui/vite.config.ts`**

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/",
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
```

- [ ] **Step 3: Create `webui/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create `webui/index.html`**

```html
<!doctype html>
<html lang="zh">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Orbbec AI Agent</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `webui/src/types.ts`**

```typescript
export interface Agent {
  id: string;
  name: string;
  domain: string;
  description: string;
  icon: string;
  owner: string;
  env: string;
  status: string; // active | maintenance | offline
  entry_url: string;
  version: string;
  tags: string[];
}

export interface Metric {
  label: string;
  value: string | number;
}

export interface Health {
  id: string;
  online: boolean | null;
  checked_at: string | null;
  latency_ms: number | null;
  version: string | null;
  metrics: Metric[];
}
```

- [ ] **Step 6: Create `webui/src/status.ts`** (pure logic — unit tested)

```typescript
import type { Agent, Health } from "./types";

export type Badge = { label: string; tone: "online" | "offline" | "maintenance" | "unknown" };

export function statusBadge(agent: Agent, health: Health | undefined): Badge {
  if (agent.status === "maintenance") return { label: "维护中", tone: "maintenance" };
  if (agent.status === "offline") return { label: "已下线", tone: "offline" };
  if (!health || health.online === null) return { label: "检测中", tone: "unknown" };
  return health.online
    ? { label: "在线", tone: "online" }
    : { label: "离线", tone: "offline" };
}
```

- [ ] **Step 7: Write the failing test `webui/src/status.test.ts`**

```typescript
import { describe, it, expect } from "vitest";
import { statusBadge } from "./status";
import type { Agent } from "./types";

const agent = (over: Partial<Agent> = {}): Agent => ({
  id: "fae", name: "AI FAE Agent", domain: "技术支持", description: "",
  icon: "🛠️", owner: "", env: "prod", status: "active",
  entry_url: "http://fae/app/", version: "", tags: [], ...over,
});

describe("statusBadge", () => {
  it("shows 检测中 when health unknown", () => {
    expect(statusBadge(agent(), undefined).tone).toBe("unknown");
  });
  it("shows 在线 when online", () => {
    expect(statusBadge(agent(), { id: "fae", online: true, checked_at: null,
      latency_ms: 1, version: null, metrics: [] }).label).toBe("在线");
  });
  it("shows 离线 when offline", () => {
    expect(statusBadge(agent(), { id: "fae", online: false, checked_at: null,
      latency_ms: null, version: null, metrics: [] }).label).toBe("离线");
  });
  it("registry maintenance overrides health", () => {
    expect(statusBadge(agent({ status: "maintenance" }), { id: "fae", online: true,
      checked_at: null, latency_ms: 1, version: null, metrics: [] }).tone).toBe("maintenance");
  });
});
```

- [ ] **Step 8: Install deps and run test to verify it passes**

Run: `cd webui && npm install && npm test`
Expected: PASS (4 passed) — `status.ts` already implemented in Step 6, so the test goes green immediately.

- [ ] **Step 9: Create `webui/src/api.ts`**

```typescript
import type { Agent, Health } from "./types";

export async function fetchAgents(): Promise<Agent[]> {
  const r = await fetch("/api/agents");
  if (!r.ok) throw new Error(`agents ${r.status}`);
  return r.json();
}

export async function fetchHealth(): Promise<Health[]> {
  const r = await fetch("/api/agents/health");
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}
```

- [ ] **Step 10: Create `webui/src/App.tsx`**

```tsx
import { useEffect, useState } from "react";
import type { Agent, Health } from "./types";
import { fetchAgents, fetchHealth } from "./api";
import { statusBadge } from "./status";

export default function App() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [health, setHealth] = useState<Record<string, Health>>({});
  const [degraded, setDegraded] = useState(false);

  useEffect(() => {
    fetchAgents().then(setAgents).catch(() => setDegraded(true));
  }, []);

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const list = await fetchHealth();
        if (stop) return;
        setHealth(Object.fromEntries(list.map((h) => [h.id, h])));
        setDegraded(false);
      } catch {
        if (!stop) setDegraded(true);
      }
    };
    tick();
    const t = setInterval(tick, 30000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  return (
    <div className="page">
      <header>
        <h1>Orbbec AI Agent</h1>
        <p className="subtitle">企业内部 AI Agent 统一入口</p>
      </header>
      {degraded && <div className="banner">平台接口暂不可用,显示的状态可能不是最新。</div>}
      <div className="grid">
        {agents.map((a) => {
          const h = health[a.id];
          const badge = statusBadge(a, h);
          return (
            <div className="card" key={a.id}>
              <div className="card-top">
                <span className="icon">{a.icon}</span>
                <span className={`badge ${badge.tone}`}>{badge.label}</span>
              </div>
              <h2>{a.name}</h2>
              <span className="domain">{a.domain}</span>
              <p className="desc">{a.description}</p>
              {h && h.metrics.length > 0 && (
                <div className="metrics">
                  {h.metrics.map((m) => (
                    <span className="chip" key={m.label}>{m.label}: {m.value}</span>
                  ))}
                </div>
              )}
              <div className="card-foot">
                {a.owner && <span className="owner">负责人: {a.owner}</span>}
                <a className="enter" href={a.entry_url} target="_blank" rel="noreferrer">
                  进入 →
                </a>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 11: Create `webui/src/main.tsx`**

```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 12: Create `webui/src/styles.css`**

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, "Microsoft YaHei", sans-serif; background: #f5f6f8; color: #1f2329; }
.page { max-width: 1100px; margin: 0 auto; padding: 40px 24px; }
header h1 { margin: 0; font-size: 28px; }
.subtitle { color: #8a8f99; margin-top: 6px; }
.banner { background: #fff4e5; border: 1px solid #ffd591; padding: 10px 14px; border-radius: 8px; margin: 16px 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 18px; margin-top: 24px; }
.card { background: #fff; border: 1px solid #e6e8eb; border-radius: 12px; padding: 18px; display: flex; flex-direction: column; gap: 8px; }
.card-top { display: flex; justify-content: space-between; align-items: center; }
.icon { font-size: 28px; }
.card h2 { margin: 4px 0 0; font-size: 18px; }
.domain { color: #5b6b7b; font-size: 13px; }
.desc { color: #4a5159; font-size: 14px; min-height: 40px; }
.metrics { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { background: #eef1f5; border-radius: 6px; padding: 2px 8px; font-size: 12px; }
.card-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
.owner { color: #8a8f99; font-size: 12px; }
.enter { color: #2f6fed; text-decoration: none; font-weight: 600; }
.badge { font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.badge.online { background: #e6f7ec; color: #18a058; }
.badge.offline { background: #fde8e8; color: #d03050; }
.badge.maintenance { background: #fff4e5; color: #d48806; }
.badge.unknown { background: #eef1f5; color: #8a8f99; }
```

- [ ] **Step 13: Build to verify frontend compiles**

Run: `cd webui && npm run build`
Expected: `dist/` produced with no TypeScript errors.

- [ ] **Step 14: Commit**

```bash
git add webui/package.json webui/vite.config.ts webui/tsconfig.json webui/index.html webui/src
git commit -m "feat: add React/Vite Portal with agent cards and live health"
```

---

### Task 8: Deploy samples, _reserved placeholders, README

**Files:**
- Create: `backend/app/_reserved/config_center/README.md`
- Create: `backend/app/_reserved/flywheel/README.md`
- Create: `backend/app/_reserved/review/README.md`
- Create: `backend/app/_reserved/gateway/README.md`
- Create: `deploy/nginx.platform.conf.example`
- Create: `deploy/docker-compose.example.yml`
- Create: `README.md`

- [ ] **Step 1: Create the four `_reserved` READMEs**

`backend/app/_reserved/config_center/README.md`:
```markdown
# config_center (阶段3,未实现)

统一管理各 Agent 的模型 / 并发 / 超时 / thinking / trace / 灰度配置,以及统一展示版本、模型、知识库更新时间。
依赖各 Agent 后续提供标准 `GET /platform/status` 契约(见设计文档 §8)。
v1 不实现;此目录仅占位,确保平台结构自解释。
```

`backend/app/_reserved/flywheel/README.md`:
```markdown
# flywheel (阶段4,未实现)

统一数据飞轮:把各 Agent 的 session/question/answer/thinking/trace/feedback/评分写入标准结构,
但业务数据按 `agent_id` 隔离。v1 不实现;此目录仅占位。
```

`backend/app/_reserved/review/README.md`:
```markdown
# review (阶段5,未实现)

统一 Review / Eval:差评回放、QA 复审、知识改进任务、核心测试集、上线门禁。
v1 不实现;此目录仅占位。
```

`backend/app/_reserved/gateway/README.md`:
```markdown
# gateway (阶段2/6,未实现)

阶段2:统一路由(host 或 path 路由,见设计文档 §10 与 deploy/nginx.platform.conf.example)。
阶段6:跨 Agent 编排 Coordinator。
v1 仅出路由规划样例,不真正接入;此目录占位。
```

- [ ] **Step 2: Create `deploy/nginx.platform.conf.example`**

```nginx
# Orbbec AI Agent Platform —— 路由规划样例 (阶段2,v1 不必启用)
#
# v1 部署:platform-api 直接在 80 端口服务 Portal(/)与 API(/api/*),
# 不需要本文件。点击卡片时浏览器直接跳到各 Agent 的 entry_url(外部地址)。
#
# 阶段2 路线 A(推荐,零改 Agent):基于 host 路由 —— 各 Agent 保留自己的 /app/
#
# server {
#     listen 80;
#     server_name portal.orbbec.internal;
#     location / { proxy_pass http://127.0.0.1:80; }   # platform-api
# }
# server {
#     listen 80;
#     server_name fae.orbbec.internal;
#     location / { proxy_pass http://<fae-host>:8000; }
# }
# server {
#     listen 80;
#     server_name admin.orbbec.internal;
#     location / { proxy_pass http://<admin-host>:8000; }
# }
#
# 阶段2 路线 B(路径路由,需改 Agent 的 Vite base 为 /fae/app/ 等并重建):
#
# server {
#     listen 80;
#     location /          { proxy_pass http://127.0.0.1:80; }       # Portal
#     location /fae/       { proxy_pass http://<fae-host>:8000/; }   # 需 Agent 配合
#     location /admin/     { proxy_pass http://<admin-host>:8000/; } # 需 Agent 配合
# }
```

- [ ] **Step 3: Create `deploy/docker-compose.example.yml`**

```yaml
# Orbbec AI Agent Platform —— 容器化样例 (v1 可不启用)
# 只编排平台自身;FAE/ADMIN 保持各自独立部署,不纳入此文件。
services:
  platform-api:
    build: ../backend
    image: orbbec-agent-platform:dev
    ports:
      - "80:80"
    environment:
      PLATFORM_REGISTRY_PATH: /app/registry.yaml
      PLATFORM_PORT: "80"
      PLATFORM_POLL_INTERVAL: "30"
      PLATFORM_STATIC_DIR: /app/static
    volumes:
      - ../registry.yaml:/app/registry.yaml:ro
      - ../webui/dist:/app/static:ro
```

- [ ] **Step 4: Create `README.md`** (repo root)

```markdown
# Orbbec AI Agent Platform

企业内部 AI Agent 统一门面与平台底座。采用**联邦模型**:把各业务 Agent(FAE / ADMIN / …)
当作独立外部产品**登记 + 展示 + 路由 + 治理**,不合并其知识库/session/业务逻辑。

- 设计文档: `docs/2026-06-24-orbbec-ai-agent-platform-v1-design.md`
- 实现计划: `docs/2026-06-24-orbbec-ai-agent-platform-v1-plan.md`

## v1 能力
- Agent Registry(`registry.yaml`,唯一真相源)
- Portal 卡片门户(React/Vite)
- 服务端健康检查(轮询各 Agent `/health`,归一化 + 缓存)
- 点击卡片跳转各 Agent 外部入口

## 本地运行
后端:
```bash
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PLATFORM_REGISTRY_PATH=../registry.yaml PLATFORM_PORT=8000 \
  uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```
前端(开发态):
```bash
cd webui && npm install && npm run dev   # http://localhost:5173,/api 代理到 :8000
```
生产:`npm run build` 后把 `webui/dist` 作为 `PLATFORM_STATIC_DIR`,platform-api 在 80 端口同时服务 Portal 与 API。

## 接入新 Agent
在 `registry.yaml` 增加一段(id/name/domain/entry_url/health),`health.type` 选 `fae`/`admin`/`generic`
或在 `backend/app/health/normalizer.py` 增加对应解析器。

## 测试
```bash
cd backend && pytest            # 后端
cd webui && npm test            # 前端
```
```

- [ ] **Step 5: Verify the app boots end-to-end against a stub agent (manual smoke)**

Run:
```bash
cd backend && . .venv/bin/activate
PLATFORM_REGISTRY_PATH=../registry.yaml PLATFORM_PORT=8000 PLATFORM_POLL_INTERVAL=5 \
  uvicorn app.main:create_app --factory --port 8000 &
sleep 2
curl -s localhost:8000/api/health
curl -s localhost:8000/api/agents | head -c 400
curl -s localhost:8000/api/agents/health | head -c 400
kill %1
```
Expected: `/api/health` → `{"status":"ok"}`; `/api/agents` → JSON list without `health`/`api_base`; `/api/agents/health` → list with `online:false` (placeholder `<fae-host>` is unreachable, which correctly reports offline rather than crashing).

- [ ] **Step 6: Commit**

```bash
git add backend/app/_reserved deploy README.md
git commit -m "docs: add _reserved placeholders, deploy samples, README"
```

---

### Task 9: Final verification

- [ ] **Step 1: Run full backend suite**

Run: `cd backend && pytest -v`
Expected: all tests PASS.

- [ ] **Step 2: Run frontend tests + build**

Run: `cd webui && npm test && npm run build`
Expected: vitest PASS, `dist/` builds clean.

- [ ] **Step 3: Confirm the two agents were never touched**

Run: `cd /home/ink/Orbbec && git -C AI-FAE-Agent status -s && git -C AI-ADMIN-Agent status -s`
Expected: no output (both repos unchanged by this work).

- [ ] **Step 4: Commit any final cleanup** (if needed)

```bash
git add -A && git commit -m "chore: v1 platform complete" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage** (each §design → task):
- §6 registry schema → `registry.yaml` (Task 1), models (Task 2), repository (Task 3) ✓
- §7 API endpoints → routes (Task 6) ✓
- §8 health poll/normalize → normalizer (Task 4), poller (Task 5) ✓
- §9 Portal frontend → Task 7 ✓
- §10 Gateway plan (samples only) → `deploy/nginx.platform.conf.example` (Task 8) ✓
- §11 deploy port 80 / single process → `main.py` static mount + docker-compose/README (Tasks 6, 8) ✓
- §12 error handling → fail-fast (Task 3), offline isolation (Task 5), degraded banner (Task 7) ✓
- §13 testing → tests in Tasks 2–7 ✓
- §14 `_reserved` placeholders → Task 8 ✓
- "do not modify agents" → verified in Task 9 Step 3 ✓

**Placeholder scan:** Registry host values `<fae-host>`/`<admin-host>` are intentional deploy-time placeholders (spec §15.3), not plan gaps. No TBD/TODO in code steps.

**Type consistency:** `HealthStatus`, `HealthCache`, `AgentEntry.public_dict()`, `normalize(type, raw)`, `statusBadge(agent, health)` names match across all tasks. Routers read `request.app.state.repo` / `request.app.state.health_cache` as set in `create_app`. Health router included before registry router so `/api/agents/health` resolves correctly (asserted in Task 6 test).
