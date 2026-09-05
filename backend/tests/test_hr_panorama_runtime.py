from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.hr.panorama_models import PanoramaRun, PublicJobSnapshot, TalentSource
from app.hr.structured_output import encode_hr_envelope, extract_hr_envelope

NOW = datetime(2026, 9, 5, 8, tzinfo=UTC)


def _source(name: str, url: str, *, owner_id: UUID) -> TalentSource:
    return TalentSource(
        uuid4(),
        owner_id,
        uuid4(),
        "company",
        f"company-{uuid4().hex}",
        name,
        (),
        (url,),
        True,
        NOW,
        NOW,
    )


def _runtime(*, state: str = "queued"):
    from app.hr.panorama_runtime import PanoramaRunRuntime

    owner_id = uuid4()
    sources = (
        _source("联合光电", "https://jobs.example.com/careers", owner_id=owner_id),
        _source("舜宇光学", "https://sunny.example.org/jobs", owner_id=owner_id),
    )
    run = PanoramaRun(
        uuid4(),
        owner_id,
        uuid4(),
        tuple(source.source_id for source in sources),
        uuid4(),
        state,
        None,
        {},
        1 if state == "queued" else 2,
        None if state == "queued" else NOW,
        None,
        NOW,
        NOW,
    )
    return PanoramaRunRuntime(run, sources)


def _payload(runtime, *, unavailable: tuple[str, ...] = ()) -> dict[str, object]:
    companies = []
    jobs = []
    facts = []
    for ordinal, source in enumerate(runtime.sources, 1):
        failed = source.canonical_name in unavailable
        companies.append(
            {
                "source_id": str(source.source_id),
                "canonical_name": source.canonical_name,
                "approved_urls": list(source.approved_urls),
                "status": "failed" if failed else "completed",
                "error_code": "SEARCH_UNAVAILABLE" if failed else None,
            }
        )
        if failed:
            continue
        public_key = f"job-{ordinal}"
        source_url = f"{source.approved_urls[0]}/{public_key}"
        jobs.append(
            {
                "company": source.canonical_name,
                "public_job_key": public_key,
                "title": f"结构工程师 {ordinal}",
                "location": "中山",
                "duty_excerpt": "负责精密结构设计",
                "requirement_excerpt": "五年以上量产经验",
                "source_url": source_url,
                "observed_at": "2026-09-05T08:00:00Z",
                "content_sha256": f"{ordinal:x}" * 64,
            }
        )
        facts.append(
            {
                "fact_id": f"f{ordinal}",
                "text": f"{source.canonical_name}公开招聘结构工程师",
                "company": source.canonical_name,
                "public_job_key": public_key,
                "source_url": source_url,
                "observed_at": "2026-09-05T08:00:00Z",
            }
        )
    return {
        "companies": companies,
        "jobs": jobs,
        "facts": facts,
        "direction_clusters": {"结构": len(jobs)},
        "inferences": (
            [{"text": "结构投入增加", "basis_fact_ids": [facts[0]["fact_id"]]}]
            if facts
            else []
        ),
        "unknowns": [{"text": "实际 HC 未公开"}],
        "summary": "公开招聘方向分析" if jobs else "所有来源暂时无法联网核验",
    }


def _answer(runtime, *, unavailable: tuple[str, ...] = ()) -> str:
    return "全景分析结果。\n\n" + encode_hr_envelope(
        "panorama_report", _payload(runtime, unavailable=unavailable)
    )


def _unchecked_answer(payload: dict[str, object]) -> str:
    document = json.dumps(
        {"schema_version": 1, "kind": "panorama_report", "payload": payload},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(document).decode("ascii").rstrip("=")
    return f"分析。\n\n<!-- platform-hr-v1:{token} -->"


def _public_resolver(hostname: str, port: int):
    assert port == 443
    return ("8.8.8.8", "2001:4860:4860::8888")


def test_panorama_envelope_requires_exact_report_jobs_and_fact_references() -> None:
    runtime = _runtime()
    payload = _payload(runtime)

    parsed = extract_hr_envelope(
        "分析。\n\n" + encode_hr_envelope("panorama_report", payload),
        "panorama_report",
    )

    assert parsed is not None
    assert parsed.payload == payload
    for mutation in (
        "top_level",
        "job_field",
        "inference_basis",
        "invalid_timestamp",
        "fact_job_mismatch",
        "completed_without_evidence",
        "unsafe_url",
    ):
        invalid = _payload(runtime)
        if mutation == "top_level":
            invalid["extra"] = True
        elif mutation == "job_field":
            del invalid["jobs"][0]["content_sha256"]  # type: ignore[index]
        elif mutation == "inference_basis":
            invalid["inferences"][0]["basis_fact_ids"] = []  # type: ignore[index]
        elif mutation == "invalid_timestamp":
            invalid["jobs"][0]["observed_at"] = "2026"  # type: ignore[index]
        elif mutation == "fact_job_mismatch":
            invalid["facts"][0]["observed_at"] = "2026-09-05T09:00:00Z"  # type: ignore[index]
        elif mutation == "completed_without_evidence":
            invalid["jobs"] = []
            invalid["facts"] = []
            invalid["inferences"] = []
        else:
            invalid["jobs"][0]["source_url"] += "#fragment"  # type: ignore[index]
            invalid["facts"][0]["source_url"] += "#fragment"  # type: ignore[index]
        with pytest.raises(ValueError, match="HR envelope invalid"):
            encode_hr_envelope("panorama_report", invalid)


def test_panorama_envelope_rejects_completed_company_without_own_evidence() -> None:
    runtime = _runtime()
    payload = _payload(runtime)
    missing_company = runtime.sources[1].canonical_name
    payload["jobs"] = [
        job for job in payload["jobs"] if job["company"] != missing_company
    ]
    payload["facts"] = [
        fact for fact in payload["facts"] if fact["company"] != missing_company
    ]

    with pytest.raises(ValueError, match="HR envelope invalid"):
        encode_hr_envelope("panorama_report", payload)


class RecordingCommands:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.turn_id = uuid4()

    def append_turn(self, owner_id, conversation_id, request_id, prompt):
        self.calls.append((owner_id, conversation_id, request_id, prompt))
        return SimpleNamespace(
            conversation=SimpleNamespace(
                conversation_id=conversation_id,
                owner_internal_user_id=owner_id,
                mode="direct_agent",
                direct_agent_id="hr-bot",
                status="active",
            ),
            turn=SimpleNamespace(turn_id=self.turn_id),
            created=len(self.calls) == 1,
        )


class RecordingRuntimeRepository:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.transitions = []
        self.snapshots = []
        self.insights = []
        self.old_reports = ["last-valid-report"]
        self.publish_calls = 0
        self.fail_insight = False

    def runtime_context(self, run_id):
        assert run_id == self.runtime.run.run_id
        return self.runtime

    def sources_for_run(self, owner_id, source_ids):
        assert owner_id == self.runtime.run.owner_id
        assert source_ids == self.runtime.run.selected_source_ids
        return self.runtime.sources

    def claim_next_runtime(self, *, claim_seconds=5):
        self.claim_seconds = claim_seconds
        return self.runtime

    def transition_run(self, command):
        self.transitions.append(command)
        return self.runtime.run

    def create_snapshot(self, command):
        self.snapshots.append(command)
        return PublicJobSnapshot(
            command.snapshot_id,
            command.owner_id,
            command.client_request_id,
            command.run_id,
            command.source_id,
            command.public_job_key,
            command.title,
            command.location,
            command.duty_excerpt,
            command.requirement_excerpt,
            command.source_url,
            command.observed_at,
            command.content_sha256,
            command.status,
            NOW,
        )

    def create_insight(self, command):
        if self.fail_insight:
            raise RuntimeError("injected insight failure")
        self.insights.append(command)
        return SimpleNamespace(insight_version_id=command.insight_version_id)

    def publish_report(self, operation):
        self.publish_calls += 1
        before = (
            list(self.transitions),
            list(self.snapshots),
            list(self.insights),
        )
        try:
            return operation(self)
        except Exception:
            self.transitions, self.snapshots, self.insights = before
            raise


def test_coordinator_submits_only_approved_scope_with_as_of_contract_and_stable_turn() -> (
    None
):
    from app.hr.panorama_runtime import PanoramaRunCoordinator

    runtime = _runtime()
    repository = RecordingRuntimeRepository(runtime)
    commands = RecordingCommands()
    coordinator = PanoramaRunCoordinator(
        repository,
        commands,
        resolver=_public_resolver,
    )

    first = coordinator.submit(runtime.run.run_id)
    second = coordinator.submit(runtime.run.run_id)

    assert first == second == runtime.run.conversation_id
    assert len(commands.calls) == 2
    assert commands.calls[0][2] == commands.calls[1][2]
    prompt = commands.calls[0][3]
    assert "联合光电" in prompt and "舜宇光学" in prompt
    assert "https://jobs.example.com/careers" in prompt
    assert "https://sunny.example.org/jobs" in prompt
    assert "未批准公司" not in prompt and "http://" not in prompt
    assert NOW.isoformat() in prompt
    assert "最多重试 1 次" in prompt
    assert "panorama_report" in prompt
    assert all(call[0] == runtime.run.owner_id for call in commands.calls)
    assert all(call[1] == runtime.run.conversation_id for call in commands.calls)


def test_new_dns_rejection_after_insert_persists_failed_run() -> None:
    from app.hr.panorama_runtime import PanoramaRunCoordinator

    runtime = _runtime()
    repository = RecordingRuntimeRepository(runtime)
    coordinator = PanoramaRunCoordinator(
        repository,
        RecordingCommands(),
        resolver=lambda _host, _port: ("10.0.0.8",),
    )

    with pytest.raises(ValueError, match="destination invalid"):
        coordinator.submit(runtime.run.run_id)

    assert repository.transitions[-1].state == "failed"
    assert repository.transitions[-1].error_code == "destination_invalid"


def test_prompt_size_rejection_is_not_mislabeled_as_destination_invalid() -> None:
    from app.hr.panorama_runtime import PanoramaRunCoordinator, PanoramaRunRuntime

    selected = _runtime()
    original = selected.sources[0]
    oversized_source = TalentSource(
        original.source_id,
        original.owner_id,
        original.client_request_id,
        original.source_kind,
        original.company_key,
        original.canonical_name,
        original.aliases,
        tuple(
            f"https://jobs.example.com/{ordinal}/" + ("a" * 1800)
            for ordinal in range(20)
        ),
        original.active,
        original.created_at,
        original.updated_at,
    )
    runtime = PanoramaRunRuntime(
        PanoramaRun(
            selected.run.run_id,
            selected.run.owner_id,
            selected.run.client_request_id,
            (oversized_source.source_id,),
            selected.run.conversation_id,
            selected.run.state,
            selected.run.error_code,
            selected.run.source_failures,
            selected.run.row_version,
            selected.run.started_at,
            selected.run.finished_at,
            selected.run.created_at,
            selected.run.updated_at,
        ),
        (oversized_source,),
    )
    repository = RecordingRuntimeRepository(runtime)
    coordinator = PanoramaRunCoordinator(
        repository,
        RecordingCommands(),
        resolver=_public_resolver,
    )

    with pytest.raises(ValueError, match="prompt too large"):
        coordinator.submit(runtime.run.run_id)

    assert repository.transitions[-1].state == "failed"
    assert repository.transitions[-1].error_code == "prompt_invalid"


def test_archived_conversation_after_run_creation_is_failed_durably() -> None:
    from app.agent_brain.conversation_repository import ConversationRepositoryConflict
    from app.hr.panorama_runtime import PanoramaRunCoordinator

    runtime = _runtime()
    repository = RecordingRuntimeRepository(runtime)

    class ArchivedCommands:
        def append_turn(self, *args):
            raise ConversationRepositoryConflict("conversation archived")

    coordinator = PanoramaRunCoordinator(
        repository,
        ArchivedCommands(),
        resolver=_public_resolver,
    )

    with pytest.raises(ConversationRepositoryConflict, match="archived"):
        coordinator.submit(runtime.run.run_id)

    assert repository.transitions[-1].state == "failed"
    assert repository.transitions[-1].error_code == "conversation_rejected"


def test_missing_conversation_after_run_creation_is_failed_durably() -> None:
    from app.agent_brain.conversation_repository import ConversationRepositoryNotFound
    from app.hr.panorama_runtime import PanoramaRunCoordinator

    runtime = _runtime()
    repository = RecordingRuntimeRepository(runtime)

    class MissingCommands:
        def append_turn(self, *args):
            raise ConversationRepositoryNotFound()

    coordinator = PanoramaRunCoordinator(
        repository,
        MissingCommands(),
        resolver=_public_resolver,
    )

    with pytest.raises(ConversationRepositoryNotFound):
        coordinator.submit(runtime.run.run_id)

    assert repository.transitions[-1].state == "failed"
    assert repository.transitions[-1].error_code == "conversation_unavailable"


def test_turn_in_progress_and_transport_failure_remain_retryable() -> None:
    from app.agent_brain.conversation_repository import (
        ConversationRepositoryError,
        ConversationTurnInProgress,
    )
    from app.hr.panorama_runtime import PanoramaRunCoordinator

    for failure in (ConversationTurnInProgress(), ConversationRepositoryError()):
        runtime = _runtime()
        repository = RecordingRuntimeRepository(runtime)

        class TemporarilyUnavailableCommands:
            def __init__(self, selected_failure: Exception) -> None:
                self.failure = selected_failure

            def append_turn(self, *args):
                raise self.failure

        coordinator = PanoramaRunCoordinator(
            repository,
            TemporarilyUnavailableCommands(failure),
            resolver=_public_resolver,
        )

        with pytest.raises(type(failure)):
            coordinator.submit(runtime.run.run_id)
        assert repository.transitions == []


def test_returned_conversation_scope_mismatch_is_failed_durably() -> None:
    from app.hr.panorama_runtime import PanoramaRunCoordinator

    runtime = _runtime()
    repository = RecordingRuntimeRepository(runtime)

    class MismatchedCommands:
        def append_turn(self, *args):
            return SimpleNamespace(
                conversation=SimpleNamespace(
                    conversation_id=uuid4(),
                    owner_internal_user_id=uuid4(),
                    mode="brain",
                    direct_agent_id=None,
                    status="archived",
                )
            )

    coordinator = PanoramaRunCoordinator(
        repository,
        MismatchedCommands(),
        resolver=_public_resolver,
    )

    with pytest.raises(ValueError, match="conversation mismatch"):
        coordinator.submit(runtime.run.run_id)

    assert repository.transitions[-1].state == "failed"
    assert repository.transitions[-1].error_code == "conversation_mismatch"


@pytest.mark.parametrize(
    "url",
    (
        "HTTPS://jobs.example.com/careers",
        "http://jobs.example.com/careers",
        "https://user:pass@jobs.example.com/careers",
        "https://localhost/careers",
        "https://127.0.0.1/careers",
        "https://[2606:4700:4700::1111]/careers",
        "https://jobs.example.com:444/careers",
        "https://jobs.example.com:99999/careers",
        "https://jobs.example.com/careers\0hidden",
    ),
)
def test_dispatch_url_policy_rejects_noncanonical_destinations(url: str) -> None:
    from app.hr.panorama_runtime import validate_panorama_destination

    with pytest.raises(ValueError, match="destination invalid"):
        validate_panorama_destination(url, resolver=_public_resolver)


def test_dispatch_url_policy_rejects_mixed_dns_and_revalidates_every_call() -> None:
    from app.hr.panorama_runtime import validate_panorama_destination

    calls: list[tuple[str, int]] = []

    def rebound(hostname: str, port: int):
        calls.append((hostname, port))
        return ("8.8.8.8",) if len(calls) == 1 else ("8.8.8.8", "10.0.0.7")

    assert (
        validate_panorama_destination(
            "https://jobs.example.com/careers", resolver=rebound
        )
        == "https://jobs.example.com/careers"
    )
    with pytest.raises(ValueError, match="destination invalid"):
        validate_panorama_destination(
            "https://jobs.example.com/careers", resolver=rebound
        )
    assert calls == [
        ("jobs.example.com", 443),
        ("jobs.example.com", 443),
    ]


@pytest.mark.parametrize(
    "address",
    ("224.0.0.1", "ff0e::1", "::ffff:10.0.0.8"),
)
def test_dispatch_url_policy_rejects_multicast_and_mapped_private_addresses(
    address: str,
) -> None:
    from app.hr.panorama_runtime import validate_panorama_destination

    with pytest.raises(ValueError, match="destination invalid"):
        validate_panorama_destination(
            "https://jobs.example.com/careers",
            resolver=lambda _host, _port: (address,),
        )


@pytest.mark.parametrize(
    "address",
    (
        "64:ff9b::a00:8",
        "64:ff9b:1::808:808",
        "2002:0808:0808::1",
        "2001:0000:4136:e378:8000:63bf:3fff:fdd2",
    ),
)
def test_dispatch_url_policy_rejects_ipv6_translation_and_tunnel_prefixes(
    address: str,
) -> None:
    from app.hr.panorama_runtime import validate_panorama_destination

    with pytest.raises(ValueError, match="destination invalid"):
        validate_panorama_destination(
            "https://jobs.example.com/careers",
            resolver=lambda _host, _port: (address,),
        )


def test_dispatch_url_policy_accepts_normal_public_ipv6_unicast() -> None:
    from app.hr.panorama_runtime import validate_panorama_destination

    selected = validate_panorama_destination(
        "https://jobs.example.com/careers",
        resolver=lambda _host, _port: ("2001:4860:4860::8888",),
    )

    assert selected == "https://jobs.example.com/careers"


@pytest.mark.parametrize(
    "fragment",
    ("%74oken=hidden", "to%6ben=hidden", "passwd=x", "sig=x"),
)
def test_dispatch_url_policy_rejects_every_fragment(fragment: str) -> None:
    from app.hr.panorama_runtime import validate_panorama_destination

    with pytest.raises(ValueError, match="destination invalid"):
        validate_panorama_destination(
            f"https://jobs.example.com/careers#{fragment}",
            resolver=_public_resolver,
        )


@pytest.mark.parametrize(
    "url",
    (
        "https://jobs.example.com/careers/%2e%2e/admin",
        "https://jobs.example.com/careers?%61pi_key=hidden",
    ),
)
def test_dispatch_url_policy_rejects_encoded_path_and_secret_query(url: str) -> None:
    from app.hr.panorama_runtime import validate_panorama_destination

    with pytest.raises(ValueError, match="destination invalid"):
        validate_panorama_destination(url, resolver=_public_resolver)


class ResultReader:
    def __init__(self, *results) -> None:
        self.results = list(results)

    def read(self, runtime):
        return self.results.pop(0) if self.results else None


class RetryCoordinator:
    def __init__(self) -> None:
        self.calls = []

    def retry(self, run_id):
        self.calls.append(run_id)
        return uuid4()

    def submit(self, run_id):
        self.calls.append(run_id)
        return uuid4()


def _execution(runtime, answer, *, attempt=1, status="completed"):
    from app.hr.panorama_runtime import PanoramaExecutionResult

    urls = tuple(job["source_url"] for job in _payload(runtime)["jobs"])
    return PanoramaExecutionResult(
        attempt=attempt,
        status=status,
        turn_id=uuid4(),
        assistant_content=answer,
        citation_urls=urls,
    )


def test_result_reader_selects_the_exact_deterministic_turn_and_citations() -> None:
    from app.hr.panorama_runtime import PanoramaConversationResultReader

    runtime = _runtime(state="running")
    calls = []
    assistant_message_id = uuid4()
    turn_id = uuid4()

    class Cursor:
        def fetchone(self):
            parameters = calls[-1][1]
            return {
                "turn_id": turn_id,
                "client_request_id": parameters[-1],
                "status": "completed",
                "assistant_message_id": assistant_message_id,
                "assistant_seq": 12,
            }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, parameters):
            calls.append((sql, parameters))
            return Cursor()

    source_url = runtime.sources[0].approved_urls[0] + "/job-1"
    message = SimpleNamespace(
        message_id=assistant_message_id,
        turn_id=turn_id,
        role="assistant",
        delivery_status="completed",
        content=_answer(runtime),
        citations=(SimpleNamespace(url=source_url),),
    )

    class Conversations:
        def messages_after(self, owner_id, conversation_id, **kwargs):
            assert owner_id == runtime.run.owner_id
            assert conversation_id == runtime.run.conversation_id
            assert kwargs == {"after": 11, "limit": 1}
            return (message,)

    reader = PanoramaConversationResultReader(
        "postgresql://unused",
        Conversations(),
        connect=lambda *args, **kwargs: Connection(),
    )

    result = reader.read(runtime)

    assert result is not None
    assert result.attempt == 2
    assert result.turn_id == turn_id
    assert result.assistant_content == message.content
    assert result.citation_urls == (source_url,)
    assert "conversation_turns" in calls[0][0]
    assert calls[0][1][:2] == (
        runtime.run.owner_id,
        runtime.run.conversation_id,
    )


def test_projector_preserves_order_and_marks_source_level_unavailability_partial() -> (
    None
):
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="running")
    answer = _answer(runtime, unavailable=(runtime.sources[1].canonical_name,))
    repository = RecordingRuntimeRepository(runtime)
    projector = PanoramaResultProjector(
        repository,
        ResultReader(_execution(runtime, answer)),
        RetryCoordinator(),
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    projected = projector.project(
        answer,
        runtime=runtime,
        citation_urls=_execution(runtime, answer).citation_urls[:1],
    )
    assert projected.direction_clusters["结构"] == 1
    assert projected.facts[0]["source_url"].startswith("https://")
    assert projected.inferences[0]["basis_fact_ids"]
    assert projector.reconcile_one() is True

    assert [item.public_job_key for item in repository.snapshots] == ["job-1"]
    assert [item["fact_id"] for item in repository.insights[0].facts] == ["f1"]
    assert repository.transitions[-1].state == "partially_completed"
    assert repository.transitions[-1].source_failures == {
        str(runtime.sources[1].source_id): "search_unavailable"
    }
    assert repository.publish_calls == 1


def test_successful_report_requires_every_evidence_url_as_an_exact_citation() -> None:
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="running")
    answer = _answer(runtime)
    projector = PanoramaResultProjector(
        RecordingRuntimeRepository(runtime),
        ResultReader(),
        RetryCoordinator(),
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    with pytest.raises(ValueError, match="citations incomplete"):
        projector.project(answer, runtime=runtime, citation_urls=())
    with pytest.raises(ValueError, match="citations incomplete"):
        projector.project(
            answer,
            runtime=runtime,
            citation_urls=(_payload(runtime)["jobs"][0]["source_url"],),  # type: ignore[index]
        )


def test_completed_company_without_own_evidence_retries_without_publishing() -> None:
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="running")
    payload = _payload(runtime)
    missing_company = runtime.sources[1].canonical_name
    payload["jobs"] = [
        job for job in payload["jobs"] if job["company"] != missing_company
    ]
    payload["facts"] = [
        fact for fact in payload["facts"] if fact["company"] != missing_company
    ]
    answer = _unchecked_answer(payload)
    repository = RecordingRuntimeRepository(runtime)
    retry = RetryCoordinator()
    projector = PanoramaResultProjector(
        repository,
        ResultReader(
            _execution(runtime, answer),
            _execution(runtime, answer, attempt=2),
        ),
        retry,
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    assert projector.reconcile_one() is True
    assert retry.calls == [runtime.run.run_id]
    assert repository.publish_calls == 0
    assert repository.snapshots == repository.insights == []
    assert repository.transitions == []

    assert projector.reconcile_one() is True
    assert repository.transitions[-1].state == "failed"
    assert repository.transitions[-1].error_code == "model_output_invalid"
    assert repository.publish_calls == 0


def test_projector_rejects_evidence_observed_after_the_run_as_of() -> None:
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="running")
    payload = _payload(runtime)
    payload["jobs"][0]["observed_at"] = "2026-09-05T08:00:01Z"  # type: ignore[index]
    payload["facts"][0]["observed_at"] = "2026-09-05T08:00:01Z"  # type: ignore[index]
    answer = "分析。\n\n" + encode_hr_envelope("panorama_report", payload)
    projector = PanoramaResultProjector(
        RecordingRuntimeRepository(runtime),
        ResultReader(),
        RetryCoordinator(),
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    with pytest.raises(ValueError, match="after panorama as-of"):
        projector.project(
            answer,
            runtime=runtime,
            citation_urls=tuple(job["source_url"] for job in payload["jobs"]),
        )


def test_projector_rejects_cross_company_source_url_and_unapproved_citation() -> None:
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="running")
    payload = _payload(runtime)
    payload["jobs"][0]["source_url"] = (  # type: ignore[index]
        runtime.sources[1].approved_urls[0] + "/borrowed"
    )
    payload["facts"][0]["source_url"] = payload["jobs"][0]["source_url"]  # type: ignore[index]
    answer = "分析。\n\n" + encode_hr_envelope("panorama_report", payload)
    projector = PanoramaResultProjector(
        RecordingRuntimeRepository(runtime),
        ResultReader(),
        RetryCoordinator(),
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    with pytest.raises(ValueError, match="URL is not approved"):
        projector.project(answer, runtime=runtime)
    with pytest.raises(ValueError, match="URL is not approved"):
        projector.project(
            _answer(runtime),
            runtime=runtime,
            citation_urls=("https://evil.example.net/report",),
        )


def test_claimed_queued_run_replays_submission_before_reading_results() -> None:
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="queued")
    repository = RecordingRuntimeRepository(runtime)
    retry = RetryCoordinator()
    projector = PanoramaResultProjector(
        repository,
        ResultReader(),
        retry,
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    assert projector.reconcile_one() is True

    assert retry.calls == [runtime.run.run_id]


def test_all_unavailable_fails_without_writing_or_replacing_previous_report() -> None:
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="running")
    answer = _answer(
        runtime,
        unavailable=tuple(source.canonical_name for source in runtime.sources),
    )
    repository = RecordingRuntimeRepository(runtime)
    projector = PanoramaResultProjector(
        repository,
        ResultReader(_execution(runtime, answer)),
        RetryCoordinator(),
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    assert projector.reconcile_one() is True

    assert repository.snapshots == []
    assert repository.insights == []
    assert repository.old_reports == ["last-valid-report"]
    assert repository.transitions[-1].state == "failed"
    assert repository.transitions[-1].error_code == "search_unavailable"


def test_publication_failure_rolls_back_snapshots_insight_and_terminal_state() -> None:
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="running")
    answer = _answer(runtime)
    repository = RecordingRuntimeRepository(runtime)
    repository.fail_insight = True
    projector = PanoramaResultProjector(
        repository,
        ResultReader(_execution(runtime, answer)),
        RetryCoordinator(),
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    with pytest.raises(RuntimeError, match="injected insight failure"):
        projector.reconcile_one()

    assert repository.publish_calls == 1
    assert repository.snapshots == repository.insights == []
    assert repository.transitions == []
    assert repository.old_reports == ["last-valid-report"]


@pytest.mark.parametrize("first_answer", [None, "", "模型没有输出结构化结果"])
def test_model_failure_or_empty_answer_retries_once_then_fails_cleanly(
    first_answer: str | None,
) -> None:
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="running")
    repository = RecordingRuntimeRepository(runtime)
    retry = RetryCoordinator()
    projector = PanoramaResultProjector(
        repository,
        ResultReader(
            _execution(
                runtime,
                first_answer,
                status="failed" if first_answer is None else "completed",
            ),
            _execution(runtime, "", attempt=2),
        ),
        retry,
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    assert projector.reconcile_one() is True
    assert retry.calls == [runtime.run.run_id]
    assert repository.transitions == []
    assert projector.reconcile_one() is True
    assert retry.calls == [runtime.run.run_id]
    assert repository.snapshots == repository.insights == []
    assert repository.transitions[-1].state == "failed"
    assert repository.old_reports == ["last-valid-report"]


def test_invalid_primary_then_archived_conversation_fails_instead_of_zombie() -> None:
    from app.agent_brain.conversation_repository import ConversationRepositoryConflict
    from app.hr.panorama_runtime import PanoramaResultProjector, PanoramaRunCoordinator

    runtime = _runtime(state="running")
    repository = RecordingRuntimeRepository(runtime)

    class ArchivedCommands:
        def append_turn(self, *args):
            raise ConversationRepositoryConflict("conversation archived")

    coordinator = PanoramaRunCoordinator(
        repository,
        ArchivedCommands(),
        resolver=_public_resolver,
    )
    projector = PanoramaResultProjector(
        repository,
        ResultReader(_execution(runtime, "模型输出无有效 envelope")),
        coordinator,
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    with pytest.raises(ConversationRepositoryConflict, match="archived"):
        projector.reconcile_one()

    assert repository.transitions[-1].state == "failed"
    assert repository.transitions[-1].error_code == "conversation_rejected"
    assert repository.publish_calls == 0
    assert repository.snapshots == repository.insights == []


def test_replaying_projection_uses_the_same_snapshot_and_insight_ids() -> None:
    from app.hr.panorama_runtime import PanoramaResultProjector

    runtime = _runtime(state="running")
    answer = _answer(runtime)
    execution = _execution(runtime, answer)
    repository = RecordingRuntimeRepository(runtime)
    projector = PanoramaResultProjector(
        repository,
        ResultReader(execution, execution),
        RetryCoordinator(),
        resolver=_public_resolver,
        model_version="claude-opus-5",
    )

    assert projector.reconcile_one() is True
    first_snapshot_ids = tuple(item.snapshot_id for item in repository.snapshots)
    first_insight_id = repository.insights[-1].insight_version_id
    repository.transitions.clear()
    assert projector.reconcile_one() is True

    assert (
        tuple(item.snapshot_id for item in repository.snapshots[2:])
        == first_snapshot_ids
    )
    assert repository.insights[-1].insight_version_id == first_insight_id
