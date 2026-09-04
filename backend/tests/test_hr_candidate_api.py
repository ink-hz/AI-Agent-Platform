from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.control_plane.models import AuthContext, Role
from app.hr.candidate_models import (
    Candidate,
    CandidateAnalysisVersion,
    CandidateDocument,
    CandidateDraft,
    ConfirmedCandidate,
    HumanFeedback,
    PositionCandidate,
)
from app.hr.candidate_repository import (
    CandidateConflict,
    CandidateNotFound,
    CandidateUnavailable,
)
from app.hr.candidate_routes import build_candidate_router
from app.hr.candidate_service import CandidateIdentityConflict, CandidateScopeViolation
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

NOW = datetime.now(UTC)


class FakeService:
    def __init__(self, owner_id):
        self.owner_id = owner_id
        self.position_id = uuid4()
        self.context_id = uuid4()
        self.candidate_record = Candidate(
            uuid4(), owner_id, "候选人甲", {"skills": ["Python"]}, NOW, NOW
        )
        self.draft_record = CandidateDraft(
            uuid4(), owner_id, self.position_id, uuid4(), uuid4(), uuid4(),
            "ready", {"stable_name": "候选人甲"}, (), None, 2, NOW, NOW,
        )
        self.document = CandidateDocument(
            uuid4(), owner_id, self.candidate_record.candidate_id,
            self.draft_record.attachment_id, self.draft_record.draft_id, "resume", 1,
            "a" * 64, "active", NOW,
        )
        self.relation = PositionCandidate(
            uuid4(), owner_id, self.position_id, self.candidate_record.candidate_id,
            self.context_id, self.draft_record.draft_id, "active", 1, NOW, NOW,
        )
        self.analysis = CandidateAnalysisVersion(
            uuid4(), owner_id, self.relation.position_candidate_id,
            self.position_id, self.candidate_record.candidate_id, self.context_id, 1,
            "match", (self.document.document_id,), (), {"summary": "适配"},
            ({"claim": "Python"},), ("规模",), (), ("说明规模",),
            "hr-r12", "model-v1", NOW,
        )
        self.feedback = HumanFeedback(
            uuid4(), owner_id, self.relation.position_candidate_id,
            self.analysis.analysis_version_id, "correction", "scale",
            "量产 100 万台", "HR 电话确认", NOW,
        )
        self.calls = []
        self.error = None

    def _result(self, value):
        if self.error is not None:
            raise self.error
        return value

    def create_drafts(self, command):
        self.calls.append(("batch", command))
        return self._result((self.draft_record,))

    def list_drafts(self, owner_id, position_id, *, batch_request_id=None):
        self.calls.append(("drafts", owner_id, position_id, batch_request_id))
        return self._result((self.draft_record,))

    def draft(self, owner_id, draft_id):
        self.calls.append(("draft", owner_id, draft_id))
        return self._result(self.draft_record)

    def retry_draft(self, command):
        self.calls.append(("retry", command))
        return self._result(self.draft_record)

    def dismiss_draft(self, command):
        self.calls.append(("dismiss", command))
        return self._result(self.draft_record)

    def confirm_draft(self, command, *, context_version_id):
        self.calls.append(("confirm", command, context_version_id))
        return self._result(
            ConfirmedCandidate(self.candidate_record, self.document, self.relation)
        )

    def list_position_candidates(self, owner_id, position_id):
        self.calls.append(("position_candidates", owner_id, position_id))
        return self._result((self.relation,))

    def candidate(self, owner_id, candidate_id):
        self.calls.append(("candidate", owner_id, candidate_id))
        return self._result(self.candidate_record)

    def documents(self, owner_id, candidate_id):
        self.calls.append(("documents", owner_id, candidate_id))
        return self._result((self.document,))

    def candidate_document(self, owner_id, document_id):
        self.calls.append(("document", owner_id, document_id))
        return self._result(self.document)

    def candidate_document_ticket(self, owner_id, document_id, purpose):
        self.calls.append(("document_ticket", owner_id, document_id, purpose))
        return self._result(SimpleNamespace(
            content_path=f"/api/v1/attachments/content/{'a' * 32}",
            expires_at=NOW,
        ))

    def position_candidate(self, owner_id, position_candidate_id):
        self.calls.append(("position_candidate", owner_id, position_candidate_id))
        return self._result(self.relation)

    def list_analyses(self, owner_id, position_candidate_id):
        self.calls.append(("analyses", owner_id, position_candidate_id))
        return self._result((self.analysis,))

    def add_analysis(self, command):
        self.calls.append(("analysis", command))
        return self._result(self.analysis)

    def list_feedback(self, owner_id, position_candidate_id):
        self.calls.append(("feedback", owner_id, position_candidate_id))
        return self._result((self.feedback,))

    def append_feedback(self, command):
        self.calls.append(("append_feedback", command))
        return self._result(self.feedback)

    def compare(self, command):
        self.calls.append(("compare", command))
        return self._result(self.analysis)


def _client(*, stale=False):
    owner_id = uuid4()
    service = FakeService(owner_id)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.auth_context = AuthContext(owner_id, Role.MEMBER, uuid4(), stale)
        return await call_next(request)

    async def require_hr_access(request: Request, *, writable=False):
        context = request.state.auth_context
        if writable and context.hard_stale_read_only:
            raise HTTPException(503, "account is read only")
        return context.internal_user_id

    app.include_router(build_candidate_router(service, require_hr_access))
    return TestClient(app), service, owner_id


def _headers():
    return {"Idempotency-Key": str(uuid4())}


def test_candidate_api_supports_batch_retry_confirm_match_compare_and_feedback() -> None:
    client, service, owner_id = _client()
    position_id = service.position_id
    draft_id = service.draft_record.draft_id
    relation_id = service.relation.position_candidate_id
    analysis_id = service.analysis.analysis_version_id

    batch = client.post(
        f"/api/hr/positions/{position_id}/candidate-drafts:batch",
        json={"attachment_ids": [str(service.draft_record.attachment_id)]},
        headers=_headers(),
    )
    retry = client.post(
        f"/api/hr/candidate-drafts/{draft_id}:retry",
        json={"expected_row_version": 2}, headers=_headers(),
    )
    confirm = client.post(
        f"/api/hr/candidate-drafts/{draft_id}:confirm",
        json={
            "expected_row_version": 2,
            "context_version_id": str(service.context_id),
            "stable_name": "候选人甲",
            "confirmed_facts": {"skills": ["Python"]},
            "merge_candidate_id": None,
        },
        headers=_headers(),
    )
    match = client.post(
        f"/api/hr/position-candidates/{relation_id}/analyses",
        json={
            "context_version_id": str(service.context_id),
            "document_ids": [str(service.document.document_id)],
            "feedback_ids": [],
            "analysis_kind": "match",
            "result": {"summary": "适配"},
            "evidence": [{"claim": "Python"}],
            "unknowns": ["规模"],
            "conflicts": [],
            "verification_questions": ["说明规模"],
            "agent_version": "hr-r12",
            "model_version": "model-v1",
        },
        headers=_headers(),
    )
    feedback = client.post(
        f"/api/hr/position-candidates/{relation_id}/feedback",
        json={
            "analysis_version_id": str(analysis_id),
            "feedback_kind": "correction",
            "conclusion_key": "scale",
            "correction": "量产 100 万台",
            "reason": "HR 电话确认",
        },
        headers=_headers(),
    )
    compare = client.post(
        f"/api/hr/positions/{position_id}/candidate-comparisons",
        json={
            "position_candidate_ids": [str(relation_id), str(uuid4())],
            "context_version_id": str(service.context_id),
            "agent_version": "hr-r12",
            "model_version": "model-v1",
        },
        headers=_headers(),
    )

    assert batch.status_code == 202
    assert retry.status_code == 200
    assert confirm.status_code == match.status_code == feedback.status_code == 201
    assert compare.status_code == 201
    assert service.calls[0][1].owner_id == owner_id
    assert confirm.json()["candidate"]["stable_name"] == "候选人甲"


def test_candidate_reads_are_private_explicit_and_do_not_leak_storage_fields() -> None:
    client, service, _ = _client()

    responses = (
        client.get(
            f"/api/hr/positions/{service.position_id}/candidate-drafts"
            f"?batch_request_id={service.draft_record.batch_request_id}"
        ),
        client.get(f"/api/hr/candidate-drafts/{service.draft_record.draft_id}"),
        client.get(f"/api/hr/positions/{service.position_id}/candidates"),
        client.get(f"/api/hr/candidates/{service.candidate_record.candidate_id}"),
        client.get(
            f"/api/hr/candidates/{service.candidate_record.candidate_id}/documents"
        ),
        client.get(f"/api/hr/candidate-documents/{service.document.document_id}"),
        client.get(
            f"/api/hr/position-candidates/{service.relation.position_candidate_id}"
        ),
        client.get(
            f"/api/hr/position-candidates/"
            f"{service.relation.position_candidate_id}/analyses"
        ),
        client.get(
            f"/api/hr/position-candidates/"
            f"{service.relation.position_candidate_id}/feedback"
        ),
    )

    assert all(response.status_code == 200 for response in responses)
    assert all(response.headers["cache-control"] == "private, no-store" for response in responses)
    serialized = " ".join(response.text for response in responses)
    assert "owner_id" not in serialized
    assert "storage" not in serialized
    assert "object_ref" not in serialized
    assert "immutable_locator" not in serialized


def test_candidate_document_ticket_is_private_owner_scoped_and_hard_stale_guarded() -> None:
    client, service, owner_id = _client()

    response = client.post(
        f"/api/hr/candidate-documents/{service.document.document_id}/ticket",
        json={"purpose": "preview"},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "content_path": f"/api/v1/attachments/content/{'a' * 32}",
        "expires_at": NOW.isoformat(),
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert service.calls == [(
        "document_ticket", owner_id, service.document.document_id, "preview"
    )]
    assert "storage" not in response.text
    assert "object_ref" not in response.text

    stale, stale_service, _ = _client(stale=True)
    blocked = stale.post(
        f"/api/hr/candidate-documents/{stale_service.document.document_id}/ticket",
        json={"purpose": "download"},
        headers=_headers(),
    )
    assert blocked.status_code == 503
    assert stale_service.calls == []


def test_candidate_document_ticket_conceals_missing_and_maps_outages() -> None:
    for error, status in (
        (CandidateNotFound(), 404),
        (CandidateUnavailable(), 503),
    ):
        client, service, _ = _client()
        service.error = error
        response = client.post(
            f"/api/hr/candidate-documents/{service.document.document_id}/ticket",
            json={"purpose": "download"},
            headers=_headers(),
        )
        assert response.status_code == status

    client, service, _ = _client()
    invalid = client.post(
        f"/api/hr/candidate-documents/{service.document.document_id}/ticket",
        json={"purpose": "inline", "storage": "s3://secret"},
        headers=_headers(),
    )
    assert invalid.status_code == 422
    assert service.calls == []


def test_feedback_shape_rejects_non_correction_text_as_422() -> None:
    client, service, _ = _client()
    response = client.post(
        f"/api/hr/position-candidates/{service.relation.position_candidate_id}/feedback",
        json={
            "analysis_version_id": str(service.analysis.analysis_version_id),
            "feedback_kind": "accepted",
            "conclusion_key": "summary",
            "correction": "must not be present",
            "reason": "reviewed",
        },
        headers=_headers(),
    )

    assert response.status_code == 422
    assert not service.calls


def test_mutations_require_writable_identity_idempotency_and_bounded_payloads() -> None:
    stale, service, _ = _client(stale=True)
    current, _, _ = _client()
    path = f"/api/hr/positions/{service.position_id}/candidate-drafts:batch"
    payload = {"attachment_ids": [str(service.draft_record.attachment_id)]}

    assert stale.post(path, json=payload, headers=_headers()).status_code == 503
    assert not service.calls
    assert current.post(path, json=payload).status_code == 422
    assert current.post(
        path,
        json={**payload, "external_recruiting_stage": "screening"},
        headers=_headers(),
    ).status_code == 422


def test_domain_validation_failures_are_projected_as_422_not_server_errors() -> None:
    client, service, _ = _client()
    confirm = client.post(
        f"/api/hr/candidate-drafts/{service.draft_record.draft_id}:confirm",
        json={
            "expected_row_version": 2,
            "context_version_id": str(service.context_id),
            "stable_name": "候选人甲",
            "confirmed_facts": {"gender": "不应进入招聘判断"},
            "merge_candidate_id": None,
        },
        headers=_headers(),
    )
    feedback = client.post(
        f"/api/hr/position-candidates/{service.relation.position_candidate_id}/feedback",
        json={
            "analysis_version_id": str(service.analysis.analysis_version_id),
            "feedback_kind": "correction",
            "conclusion_key": "scale",
            "correction": None,
            "reason": "HR 电话确认",
        },
        headers=_headers(),
    )
    comparison = client.post(
        f"/api/hr/positions/{service.position_id}/candidate-comparisons",
        json={
            "position_candidate_ids": [
                str(service.relation.position_candidate_id),
                str(service.relation.position_candidate_id),
            ],
            "context_version_id": str(service.context_id),
            "agent_version": "hr-r12",
            "model_version": "model-v1",
        },
        headers=_headers(),
    )

    assert confirm.status_code == feedback.status_code == comparison.status_code == 422
    assert not service.calls


def test_candidate_errors_have_stable_concealed_http_projection() -> None:
    for error, status in (
        (CandidateNotFound(), 404),
        (CandidateScopeViolation(), 404),
        (CandidateConflict(), 409),
        (CandidateIdentityConflict(), 409),
        (CandidateUnavailable(), 503),
    ):
        client, service, _ = _client()
        service.error = error
        response = client.get(f"/api/hr/candidates/{uuid4()}")
        assert response.status_code == status
        assert response.json() == {
            "detail": {
                404: "HR candidate not found",
                409: "HR candidate conflict",
                503: "HR candidate unavailable",
            }[status]
        }


def test_router_rejects_incomplete_dependencies_and_invalid_owner_result() -> None:
    async def invalid_owner(*_args, **_kwargs):
        return SimpleNamespace()

    service = FakeService(uuid4())
    app = FastAPI()
    app.include_router(build_candidate_router(service, invalid_owner))
    client = TestClient(app)
    assert client.get(f"/api/hr/candidates/{uuid4()}").status_code == 401

    try:
        build_candidate_router(SimpleNamespace(), invalid_owner)
    except ValueError as error:
        assert str(error) == "candidate service required"
    else:
        raise AssertionError("incomplete candidate service accepted")
