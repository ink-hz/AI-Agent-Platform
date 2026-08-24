from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthSpec(BaseModel):
    url: str
    type: str = "generic"


class ReplayTarget(BaseModel):
    environment: Literal["dev"]
    api_base: str
    health_url: str
    credential_ref: str


class ReviewEvidenceConfig(BaseModel):
    repository_path: str
    release_manifest_dir: str


class AgentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    name: str
    domain: str = ""
    description: str = ""
    icon: str = ""
    owner: str = ""
    env: str = "prod"
    status: str = "active"
    entry_url: str
    health: HealthSpec
    api_base: str | None = None
    version: str = ""
    tags: list[str] = Field(default_factory=list)
    flywheel_agent_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )
    replay_targets: list[ReplayTarget] = Field(default_factory=list)
    review_evidence: ReviewEvidenceConfig | None = None

    def public_dict(self) -> dict:
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
