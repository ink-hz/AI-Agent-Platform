from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from pydantic import ValidationError

from .cases import ACTION_CASE_ID, BASE_UPSTREAM_HTTP_CASE_IDS
from .models import (
    CONTRACT_VERSION,
    TERMINAL_EVENT_KINDS,
    ActionExecuteReceipt,
    ActionDigestInput,
    ActionProposal,
    CancelReceipt,
    CapabilitiesResponse,
    CreateTaskReceipt,
    ErrorEnvelope,
    EventPage,
    HealthResponse,
    MessageReceipt,
    TaskResponse,
    TokenBrokerRequest,
    action_digest,
)
from .token_broker import TaskTokenBroker, TokenBrokerError

API_PREFIX = "/internal/platform/v1"


class ContractViolation(AssertionError):
    """Raised when the target violates HTTP Task Contract v1."""


@dataclass(frozen=True)
class ContractReport:
    contract_version: str
    executed_cases: tuple[str, ...]


class TokenIssuer(Protocol):
    def issue(self, request: TokenBrokerRequest) -> str: ...


def require_supported_python(version: Sequence[int] = sys.version_info) -> None:
    if tuple(version[:2]) < (3, 11):
        raise ContractViolation("HTTP Task Contract v1 requires Python 3.11 or newer")


def _json_model_bytes(document: Mapping[str, object]) -> bytes:
    return json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")


def validate_event_page(
    document: Mapping[str, object], *, after: int, limit: int
) -> EventPage:
    try:
        page = EventPage.model_validate_json(_json_model_bytes(document))
    except (TypeError, ValueError, ValidationError) as exc:
        raise ContractViolation(f"invalid event kind or event page: {exc}") from exc
    if len(page.events) > limit:
        raise ContractViolation("finite event page exceeded requested limit")
    expected = after + 1
    saw_terminal = False
    for index, event in enumerate(page.events):
        if event.seq != expected:
            raise ContractViolation(
                "event sequence must be strictly continuous from after + 1"
            )
        expected += 1
        if event.kind in TERMINAL_EVENT_KINDS:
            if index != len(page.events) - 1:
                raise ContractViolation("terminal event must be last in its page")
            saw_terminal = True
    expected_next = page.events[-1].seq if page.events else after
    if page.next_after != expected_next:
        raise ContractViolation(
            "next_after must equal the last returned seq or request after"
        )
    if saw_terminal and not page.terminal:
        raise ContractViolation("a terminal event requires terminal=true")
    return page


class ContractRunner:
    def __init__(
        self,
        *,
        base_url: str,
        token_broker: TokenIssuer,
        agent_id: str,
        authorized_scopes: Sequence[str],
        transport: httpx.BaseTransport | None = None,
        max_request_seconds: float = 2.0,
        request_timeout_seconds: float = 5.0,
        case_timeout_seconds: float = 15.0,
        slow_case_timeout_seconds: float = 30.0,
        profile_timeout_seconds: float = 180.0,
        poll_interval_seconds: float = 0.02,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url is required")
        if not agent_id.strip():
            raise ValueError("agent_id is required")
        scopes = tuple(sorted(set(authorized_scopes)))
        if not scopes:
            raise ValueError("at least one authorized scope is required")
        if (
            max_request_seconds <= 0
            or request_timeout_seconds <= 0
            or case_timeout_seconds <= 0
            or slow_case_timeout_seconds < case_timeout_seconds
            or profile_timeout_seconds < slow_case_timeout_seconds
        ):
            raise ValueError("contract timeouts must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._max_request_seconds = max_request_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._case_timeout_seconds = case_timeout_seconds
        self._slow_case_timeout_seconds = slow_case_timeout_seconds
        self._profile_timeout_seconds = profile_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._token_broker = token_broker
        self._agent_id = agent_id
        self._authorized_scopes = scopes
        self._probe_task_id = uuid5(NAMESPACE_URL, f"{base_url.rstrip('/')}:{agent_id}")
        self._task_deadlines: dict[UUID, datetime] = {}
        self._downstream_task_ids: dict[UUID, str] = {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "X-Orbbec-Task-Contract": CONTRACT_VERSION,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(request_timeout_seconds),
            transport=transport,
            trust_env=False,
        )
        self._capability_version = 0

    def run(self) -> ContractReport:
        require_supported_python()
        profile_started = monotonic()
        capabilities = self._request_json(
            "GET", f"{API_PREFIX}/capabilities", expected={200}
        )
        parsed_capabilities = self._validate_capabilities(capabilities)
        health = self._parse_model(
            HealthResponse,
            self._request_json("GET", f"{API_PREFIX}/health", expected={200}),
            "health",
        )
        if health.capability_version != parsed_capabilities.capability_version:
            raise ContractViolation("health capability_version does not match capabilities")
        executed = [
            "health",
            *self._check_authorization_matrix(),
            self._check_create_idempotency_capability(),
            self._check_event_pages_sequence_terminal(),
            self._check_follow_up(),
            self._check_cancel(),
            self._check_deadline(),
        ]
        if parsed_capabilities.supports_actions:
            executed.append(self._check_action_proposal_execute())
        expected_cases = (
            (*BASE_UPSTREAM_HTTP_CASE_IDS, ACTION_CASE_ID)
            if parsed_capabilities.supports_actions
            else BASE_UPSTREAM_HTTP_CASE_IDS
        )
        if tuple(executed) != tuple(expected_cases):
            raise ContractViolation("upstream_http case catalog drifted")
        if monotonic() - profile_started > self._profile_timeout_seconds:
            raise ContractViolation("upstream_http profile exceeded 180 seconds")
        return ContractReport(CONTRACT_VERSION, tuple(executed))

    def close(self) -> None:
        self._client.close()

    def _validate_capabilities(
        self, document: Mapping[str, object]
    ) -> CapabilitiesResponse:
        parsed = self._parse_model(CapabilitiesResponse, document, "capabilities")
        if parsed.agent_id != self._agent_id:
            raise ContractViolation("capabilities returned the wrong agent_id")
        self._capability_version = parsed.capability_version
        return parsed

    def _task_payload(
        self,
        turn_ref: str,
        *,
        task_id: UUID | None = None,
        idempotency_key: str | None = None,
        deadline_at: datetime | None = None,
    ) -> dict[str, object]:
        platform_task_id = task_id or uuid4()
        deadline = deadline_at or datetime.now(UTC) + timedelta(minutes=5)
        return {
            "contract_version": CONTRACT_VERSION,
            "platform_task_id": str(platform_task_id),
            "conversation_ref": "orbbec-http-task-contract-v1",
            "turn_ref": turn_ref,
            "objective": f"Execute deterministic HTTP Task Contract fixture: {turn_ref}",
            "context_excerpt": ["black-box contract verification"],
            "constraints": ["use deterministic contract fixture behavior"],
            "attachment_refs": [],
            "expected_output": "contract fixture response",
            "capability_version": self._capability_version,
            "idempotency_key": idempotency_key or f"contract-create:{platform_task_id}",
            "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
            "authorized_scopes": ["contract.test"],
        }

    def _create(
        self, turn_ref: str, *, deadline_at: datetime | None = None
    ) -> tuple[UUID, dict[str, object]]:
        task_id = uuid4()
        payload = self._task_payload(turn_ref, task_id=task_id, deadline_at=deadline_at)
        receipt = self._request_json(
            "POST", f"{API_PREFIX}/tasks", expected={202}, json_body=payload
        )
        parsed = self._validate_create_receipt(receipt, duplicate=False)
        self._downstream_task_ids[task_id] = parsed.downstream_task_id
        return task_id, receipt

    def _validate_create_receipt(
        self, document: Mapping[str, object], *, duplicate: bool
    ) -> CreateTaskReceipt:
        parsed = self._parse_model(CreateTaskReceipt, document, "create receipt")
        if parsed.duplicate is not duplicate:
            raise ContractViolation("create duplicate flag is incorrect")
        return parsed

    def _check_authorization_matrix(self) -> tuple[str, ...]:
        cases = (
            ("missing", 401, "auth_missing"),
            ("expired", 401, "auth_expired"),
            ("wrong_audience", 401, "auth_wrong_audience"),
            ("retired_kid", 401, "auth_retired_kid"),
            ("wrong_scope", 403, "auth_wrong_scope"),
            ("wrong_task_binding", 403, "auth_wrong_task_binding"),
        )
        executed: list[str] = []
        for profile, status, case_id in cases:
            task_id = uuid4()
            payload = self._task_payload(
                f"fixture:{case_id}",
                task_id=task_id,
                idempotency_key=f"fixture:{case_id}:{task_id}",
            )
            response = self._request(
                "POST",
                f"{API_PREFIX}/tasks",
                expected={status},
                json_body=payload,
                token_profile="valid" if profile == "missing" else profile,
                omit_token=profile == "missing",
            )
            self._parse_error(response)
            valid = self._request_json(
                "POST", f"{API_PREFIX}/tasks", expected={202}, json_body=payload
            )
            receipt = self._validate_create_receipt(valid, duplicate=False)
            self._downstream_task_ids[task_id] = receipt.downstream_task_id
            executed.append(case_id)
        return tuple(executed)

    def _check_create_idempotency_capability(self) -> str:
        task_id = uuid4()
        payload = self._task_payload("fixture/create-idempotency", task_id=task_id)
        first = self._request_json(
            "POST", f"{API_PREFIX}/tasks", expected={202}, json_body=payload
        )
        first_receipt = self._validate_create_receipt(first, duplicate=False)
        self._downstream_task_ids[task_id] = first_receipt.downstream_task_id
        duplicate = self._request_json(
            "POST", f"{API_PREFIX}/tasks", expected={202}, json_body=payload
        )
        self._validate_create_receipt(duplicate, duplicate=True)
        if duplicate["downstream_task_id"] != first["downstream_task_id"]:
            raise ContractViolation("idempotent create changed downstream_task_id")

        collision = {
            **payload,
            "objective": "different payload with the same idempotency key",
        }
        response = self._request(
            "POST", f"{API_PREFIX}/tasks", expected={409}, json_body=collision
        )
        self._assert_error(response, "idempotency_conflict")

        stale_task_id = uuid4()
        stale = self._task_payload(
            "fixture/stale-capability",
            task_id=stale_task_id,
            idempotency_key=f"contract-stale:{stale_task_id}",
        )
        stale["capability_version"] = self._capability_version + 1
        response = self._request(
            "POST", f"{API_PREFIX}/tasks", expected={409}, json_body=stale
        )
        error = self._assert_error(response, "capability_changed")
        if error.get("current_capability_version") != self._capability_version:
            raise ContractViolation(
                "capability_changed must return current_capability_version"
            )
        if error.get("must_refresh_capabilities") is not True:
            raise ContractViolation(
                "capability_changed must require capability refresh"
            )
        corrected = {**stale, "capability_version": self._capability_version}
        corrected_receipt = self._validate_create_receipt(
            self._request_json(
                "POST", f"{API_PREFIX}/tasks", expected={202}, json_body=corrected
            ),
            duplicate=False,
        )
        self._downstream_task_ids[stale_task_id] = corrected_receipt.downstream_task_id
        return "create_idempotency_capability"

    def _event_page(self, task_id: UUID, *, after: int, limit: int) -> EventPage:
        response = self._request(
            "GET",
            f"{API_PREFIX}/tasks/{task_id}/events",
            expected={200},
            params={"after": after, "limit": limit, "wait_seconds": 0},
        )
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("application/json"):
            raise ContractViolation(
                "event endpoint must return a finite JSON page, not a stream"
            )
        page = validate_event_page(
            self._response_json(response), after=after, limit=limit
        )
        expected_downstream = self._downstream_task_ids.get(task_id)
        if expected_downstream is not None and page.downstream_task_id != expected_downstream:
            raise ContractViolation("event page changed downstream_task_id")
        return page

    def _wait_for_events(self, task_id: UUID, *, after: int, limit: int) -> EventPage:
        expires_at = monotonic() + self._case_timeout_seconds
        while True:
            page = self._event_page(task_id, after=after, limit=limit)
            if page.events:
                return page
            if page.terminal:
                raise ContractViolation("task became terminal without a terminal event")
            if monotonic() >= expires_at:
                raise ContractViolation(
                    "timed out waiting on finite nonblocking event pages"
                )
            sleep(self._poll_interval_seconds)

    def _observe_terminal_stability(self, task_id: UUID, *, after: int) -> None:
        started = monotonic()
        for read_index in range(3):
            page = self._event_page(task_id, after=after, limit=100)
            if page.events or page.next_after != after or not page.terminal:
                raise ContractViolation("terminal task changed after completion")
            if read_index < 2:
                remaining = max(0.0, 0.5 - (monotonic() - started))
                sleep(remaining / (2 - read_index))
        if monotonic() - started < 0.5:
            raise ContractViolation("terminal stability window was shorter than 500ms")

    def _check_event_pages_sequence_terminal(self) -> str:
        task_id, _ = self._create("fixture/event-sequence-terminal")
        first = self._wait_for_events(task_id, after=0, limit=2)
        if [event.seq for event in first.events] != [1, 2] or first.terminal:
            raise ContractViolation(
                "event fixture must expose a finite nonterminal first page"
            )
        second = self._wait_for_events(task_id, after=2, limit=2)
        if [event.kind for event in second.events] != ["result"] or not second.terminal:
            raise ContractViolation("event fixture must terminate with result")
        replay = self._event_page(task_id, after=2, limit=2)
        if replay != second:
            raise ContractViolation("event page replay from after was not exact")
        self._observe_terminal_stability(task_id, after=3)
        task = self._parse_model(
            TaskResponse,
            self._request_json("GET", f"{API_PREFIX}/tasks/{task_id}", expected={200}),
            "task",
        )
        if task.status != "completed":
            raise ContractViolation(
                "result terminal event must project status=completed"
            )
        return "finite_event_pages_sequence_terminal"

    def _check_follow_up(self) -> str:
        task_id, _ = self._create("fixture/follow-up")
        body: dict[str, object] = {
            "contract_version": CONTRACT_VERSION,
            "message_seq": 1,
            "content": "deterministic follow-up",
            "attachment_refs": [],
            "idempotency_key": f"contract-message:{task_id}:1",
        }
        first = self._request_json(
            "POST",
            f"{API_PREFIX}/tasks/{task_id}/messages",
            expected={202},
            json_body=body,
        )
        duplicate = self._request_json(
            "POST",
            f"{API_PREFIX}/tasks/{task_id}/messages",
            expected={202},
            json_body=body,
        )
        first_receipt = self._parse_model(MessageReceipt, first, "message receipt")
        duplicate_receipt = self._parse_model(
            MessageReceipt, duplicate, "message receipt"
        )
        if first_receipt.message_seq != 1 or first_receipt.duplicate is not False:
            raise ContractViolation("first follow-up message was not accepted")
        if (
            duplicate_receipt.message_seq != 1
            or duplicate_receipt.duplicate is not True
        ):
            raise ContractViolation("follow-up message replay was not idempotent")
        if first_receipt.downstream_task_id != duplicate_receipt.downstream_task_id:
            raise ContractViolation("follow-up replay changed downstream_task_id")
        collision = {**body, "content": "different content"}
        response = self._request(
            "POST",
            f"{API_PREFIX}/tasks/{task_id}/messages",
            expected={409},
            json_body=collision,
        )
        self._assert_error(response, "message_sequence_conflict")
        return "follow_up"

    def _check_cancel(self) -> str:
        task_id, _ = self._create("fixture/cancel")
        body = {
            "contract_version": CONTRACT_VERSION,
            "idempotency_key": f"contract-cancel:{task_id}",
        }
        first = self._request_json(
            "POST",
            f"{API_PREFIX}/tasks/{task_id}/cancel",
            expected={200, 202},
            json_body=body,
        )
        replay = self._request_json(
            "POST",
            f"{API_PREFIX}/tasks/{task_id}/cancel",
            expected={200, 202},
            json_body=body,
        )
        first_receipt = self._parse_model(CancelReceipt, first, "cancel receipt")
        replay_receipt = self._parse_model(CancelReceipt, replay, "cancel receipt")
        if first_receipt.duplicate or not replay_receipt.duplicate:
            raise ContractViolation("cancel duplicate flags are incorrect")
        if first_receipt.cancel_request_id != replay_receipt.cancel_request_id:
            raise ContractViolation("repeated cancel changed cancel_request_id")
        page = self._event_page(task_id, after=0, limit=100)
        if [event.kind for event in page.events] != ["cancelled"] or not page.terminal:
            raise ContractViolation(
                "cancel fixture must emit exactly one cancelled terminal event"
            )
        self._observe_terminal_stability(task_id, after=1)
        task = self._parse_model(
            TaskResponse,
            self._request_json("GET", f"{API_PREFIX}/tasks/{task_id}", expected={200}),
            "task",
        )
        if task.status != "cancelled":
            raise ContractViolation(
                "cancelled terminal event must project status=cancelled"
            )
        return "cancel"

    def _check_deadline(self) -> str:
        expired_task_id = uuid4()
        expired = self._task_payload(
            "fixture/expired-deadline",
            task_id=expired_task_id,
            idempotency_key=f"contract-expired:{expired_task_id}",
            deadline_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        response = self._request(
            "POST", f"{API_PREFIX}/tasks", expected={409}, json_body=expired
        )
        self._assert_error(response, "deadline_expired")
        corrected_deadline = datetime.now(UTC) + timedelta(minutes=5)
        corrected = {
            **expired,
            "deadline_at": corrected_deadline.isoformat().replace("+00:00", "Z"),
        }
        corrected_receipt = self._validate_create_receipt(
            self._request_json(
                "POST", f"{API_PREFIX}/tasks", expected={202}, json_body=corrected
            ),
            duplicate=False,
        )
        self._downstream_task_ids[expired_task_id] = corrected_receipt.downstream_task_id

        deadline = datetime.now(UTC) + timedelta(milliseconds=100)
        task_id, _ = self._create("fixture/deadline", deadline_at=deadline)
        page = self._wait_for_events(task_id, after=0, limit=100)
        if [event.kind for event in page.events] != ["timeout"] or not page.terminal:
            raise ContractViolation(
                "deadline fixture must emit exactly one timeout terminal event"
            )
        if page.events[0].created_at < deadline:
            raise ContractViolation(
                "timeout event was emitted before the task deadline"
            )
        self._observe_terminal_stability(task_id, after=1)
        task = self._parse_model(
            TaskResponse,
            self._request_json("GET", f"{API_PREFIX}/tasks/{task_id}", expected={200}),
            "task",
        )
        if task.status != "timed_out":
            raise ContractViolation(
                "timeout terminal event must project status=timed_out"
            )
        return "deadline"

    def _check_action_proposal_execute(self) -> str:
        task_id, _ = self._create("fixture/action")
        page = self._event_page(task_id, after=0, limit=100)
        if len(page.events) != 1 or page.events[0].kind != "action_required":
            raise ContractViolation(
                "action fixture must emit one action_required proposal"
            )
        try:
            proposal = ActionProposal.model_validate_json(
                json.dumps(page.events[0].payload, ensure_ascii=False).encode("utf-8")
            )
        except ValidationError as exc:
            raise ContractViolation(f"invalid action proposal: {exc}") from exc
        expected_action_id = uuid5(task_id, f"action:{proposal.action_seq}")
        if proposal.action_id != expected_action_id:
            raise ContractViolation(
                "action_id must be uuid5(platform_task_id, action:<seq>)"
            )
        expected_digest = action_digest(
            ActionDigestInput(
                platform_task_id=task_id,
                action_seq=proposal.action_seq,
                action_kind=proposal.action_kind,
                parameters=proposal.parameters,
            )
        )
        if proposal.action_digest != expected_digest:
            raise ContractViolation(
                "action proposal digest does not match canonical parameters"
            )
        body = {
            "contract_version": CONTRACT_VERSION,
            "action_id": str(proposal.action_id),
            "action_digest": proposal.action_digest,
            "idempotency_key": f"contract-execute:{proposal.action_id}",
        }
        endpoint = f"{API_PREFIX}/tasks/{task_id}/actions/{proposal.action_id}/execute"
        first = self._request_json(
            "POST", endpoint, expected={200, 202}, json_body=body
        )
        replay = self._request_json(
            "POST", endpoint, expected={200, 202}, json_body=body
        )
        first_receipt = self._parse_model(
            ActionExecuteReceipt, first, "action execute receipt"
        )
        replay_receipt = self._parse_model(
            ActionExecuteReceipt, replay, "action execute receipt"
        )
        if first_receipt.duplicate or not replay_receipt.duplicate:
            raise ContractViolation("action execute duplicate flags are incorrect")
        if first_receipt.execution_id != replay_receipt.execution_id:
            raise ContractViolation("repeated action execute changed execution_id")
        bad_digest = (
            "1" if proposal.action_digest[0] == "0" else "0"
        ) + proposal.action_digest[1:]
        mismatch = {
            **body,
            "action_digest": bad_digest,
            "idempotency_key": body["idempotency_key"] + ":bad",
        }
        response = self._request("POST", endpoint, expected={409}, json_body=mismatch)
        self._assert_error(response, "action_digest_mismatch")
        result_page = self._wait_for_events(task_id, after=1, limit=100)
        if [event.kind for event in result_page.events] != ["result"]:
            raise ContractViolation("action execution did not produce one result")
        payload = result_page.events[0].payload
        if payload.get("execution_id") != first_receipt.execution_id:
            raise ContractViolation("action result changed execution_id")
        if payload.get("fixture_business_effect_count") != 1:
            raise ContractViolation("duplicate action execute repeated the business effect")
        self._observe_terminal_stability(task_id, after=2)
        return "action_proposal_execute"

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        json_body: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        return self._response_json(
            self._request(method, path, expected=expected, json_body=json_body)
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        json_body: Mapping[str, object] | None = None,
        params: Mapping[str, object] | None = None,
        token_profile: str = "valid",
        omit_token: bool = False,
    ) -> httpx.Response:
        started = monotonic()
        must_be_nonblocking = (method == "POST" and path == f"{API_PREFIX}/tasks") or (
            method == "GET" and path.endswith("/events")
        )
        request_timeout = min(self._max_request_seconds, self._request_timeout_seconds)
        task_id, task_deadline = self._request_task_identity(path, json_body)
        capability_version = self._capability_version or 1
        headers: dict[str, str] = {}
        if not omit_token:
            try:
                token = self._token_broker.issue(
                    TokenBrokerRequest(
                        profile=token_profile,
                        agent_id=self._agent_id,
                        platform_task_id=task_id,
                        capability_version=capability_version,
                        authorized_scopes=self._authorized_scopes,
                        task_deadline_at=task_deadline,
                        action_execution_deadline_at=None,
                    )
                )
            except TokenBrokerError as exc:
                raise ContractViolation("task token broker failed") from exc
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self._client.request(
                method,
                path,
                json=json_body,
                params=params,
                headers=headers,
                timeout=request_timeout,
            )
        except httpx.HTTPError as exc:
            raise ContractViolation(
                f"HTTP request failed for {method} {path}: {exc}"
            ) from exc
        elapsed = monotonic() - started
        if must_be_nonblocking and elapsed > self._max_request_seconds:
            raise ContractViolation(
                f"{method} {path} blocked for {elapsed:.3f}s; contract limit is "
                f"{self._max_request_seconds:.3f}s"
            )
        if response.status_code not in expected:
            raise ContractViolation(
                f"{method} {path} returned HTTP {response.status_code}; expected {sorted(expected)}"
            )
        return response

    def _request_task_identity(
        self, path: str, json_body: Mapping[str, object] | None
    ) -> tuple[UUID, datetime]:
        if path == f"{API_PREFIX}/tasks" and json_body is not None:
            task_id = UUID(str(json_body["platform_task_id"]))
            deadline = datetime.fromisoformat(
                str(json_body["deadline_at"]).replace("Z", "+00:00")
            )
            self._task_deadlines[task_id] = deadline
            return task_id, deadline
        if "/tasks/" in path:
            raw_task_id = path.split("/tasks/", 1)[1].split("/", 1)[0]
            task_id = UUID(raw_task_id)
            deadline = self._task_deadlines.get(
                task_id, datetime.now(UTC) + timedelta(minutes=5)
            )
            return task_id, deadline
        return self._probe_task_id, datetime.now(UTC) + timedelta(minutes=5)

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        try:
            document = response.json()
        except ValueError as exc:
            raise ContractViolation("HTTP response was not valid JSON") from exc
        if not isinstance(document, dict):
            raise ContractViolation("HTTP response JSON must be an object")
        return document

    @staticmethod
    def _parse_model(
        model: type[Any], document: Mapping[str, object], label: str
    ) -> Any:
        try:
            return model.model_validate_json(_json_model_bytes(document))
        except (TypeError, ValueError, ValidationError) as exc:
            raise ContractViolation(f"invalid {label} response: {exc}") from exc

    def _parse_error(self, response: httpx.Response) -> ErrorEnvelope:
        return self._parse_model(
            ErrorEnvelope, self._response_json(response), "error envelope"
        )

    def _assert_error(self, response: httpx.Response, code: str) -> dict[str, Any]:
        envelope = self._parse_error(response)
        if envelope.error.code != code:
            raise ContractViolation(f"expected error code {code}")
        return envelope.error.details.model_dump(exclude_none=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Orbbec HTTP Task Contract v1")
    parser.add_argument("--base-url", required=True, help="target service base URL")
    parser.add_argument(
        "--token-broker",
        required=True,
        type=Path,
        help="absolute path to the local Task Token Broker executable",
    )
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--scope", action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        require_supported_python()
        args = _parser().parse_args(argv)
        runner = ContractRunner(
            base_url=args.base_url,
            token_broker=TaskTokenBroker(args.token_broker),
            agent_id=args.agent_id,
            authorized_scopes=args.scope,
        )
        try:
            report = runner.run()
        finally:
            runner.close()
    except (ContractViolation, ValueError) as exc:
        print(f"contract failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "contract_version": report.contract_version,
                "executed_cases": report.executed_cases,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
