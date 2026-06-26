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
    status: str = "active"
    entry_url: str
    health: HealthSpec
    api_base: str | None = None
    version: str = ""
    tags: list[str] = Field(default_factory=list)

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
