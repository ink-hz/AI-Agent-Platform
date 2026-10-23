from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Commit = Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
Text = Annotated[str, Field(min_length=1, max_length=600)]
Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$')]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SearchRequest(StrictModel):
    q: Annotated[str, Field(max_length=200)] = ''
    repository: Annotated[str, Field(max_length=128)] | None = None
    path: Annotated[str, Field(max_length=512)] | None = None
    symbol: Annotated[str, Field(max_length=200)] | None = None
    device: Annotated[str, Field(max_length=200)] | None = None
    platform: Annotated[str, Field(max_length=200)] | None = None
    limit: Annotated[int, Field(ge=1, le=10)] = 10


class PageRequest(StrictModel):
    page_id: Identifier


class ChangeRequest(StrictModel):
    repository: Annotated[str, Field(min_length=1, max_length=128)]
    base_commit: Commit
    target_commit: Commit


class AnalysisRequest(ChangeRequest):
    objective: Annotated[str, Field(min_length=1, max_length=4000)]


class ProposedImpact(StrictModel):
    area: Annotated[str, Field(min_length=1, max_length=200)]
    rationale: Text
    claim_ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]


class ProposedRegression(StrictModel):
    scenario: Text
    preconditions: Annotated[list[Text], Field(max_length=3)]
    steps: Annotated[list[Text], Field(min_length=1, max_length=3)]
    expected_behavior: Text
    claim_ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]


class AnalysisProposal(StrictModel):
    summary: Text
    impacts: Annotated[list[ProposedImpact], Field(max_length=3)]
    regressions: Annotated[list[ProposedRegression], Field(max_length=3)]
    questions: Annotated[list[Text], Field(max_length=6)]
    limitations: Annotated[list[Text], Field(min_length=1, max_length=6)]
