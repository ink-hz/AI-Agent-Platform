"""Durable model/tool loop; business reasoning belongs to the configured model."""
from __future__ import annotations

from datetime import UTC, datetime
from threading import Event
from time import monotonic
from uuid import uuid4

from .model import ModelError, collect_reply
from .observability import emit_log, guard_dependency_debug_logging
from .types import ContextRebuildRequired, HrAgentProblem, WorkPaused, WorkerIdentity


def run_work(repository, model, resources, fence, *, context_builder=None,
             tool_executor=None, stop_event=None, observer=None):
    """Resume committed work before creating any new provider request.

    ``observer`` is an internal dependency used by process fault fixtures. It is
    never loaded from task input, model arguments, environment, or HTTP.
    """
    if context_builder is None:
        from .context import build_model_context
        context_builder = build_model_context
    guard_dependency_debug_logging()
    stop_event = stop_event or Event()
    observe = observer or (lambda event, identity: None)
    worker = WorkerIdentity(fence.worker_id, getattr(repository.settings, 'configuration_revision', 'a1-test'))
    def log(event, attempt_id, *, elapsed=0, usage=None, error=None):
        emit_log({'at': datetime.now(UTC).isoformat(), 'event': event,
                  'work_id': str(fence.work_id), 'attempt_id': str(attempt_id),
                  'operation_id': None, 'state': None, 'duration_ms': int(elapsed * 1000),
                  'input_tokens': usage.input_total if usage else None,
                  'output_tokens': usage.output_total if usage else None,
                  'error_code': error, 'profile_revision': worker.profile_revision})

    while not stop_event.is_set():
        try:
            action = repository.next_action(fence)
            if action.kind in {'wait', 'done'}:
                return repository.worker_view(fence)
            if action.kind == 'project_answer':
                return repository.finish_work(fence, action.attempt_id)
            if action.kind == 'project_summary':
                repository.commit_summary(fence, action.attempt_id,
                                          repository.summary_provenance(fence, action.attempt_id))
                observe('summary_committed', action.attempt_id)
                continue
            if action.kind == 'execute_tool':
                if tool_executor is None:
                    from .tools import execute_tool
                    tool_executor = execute_tool
                tool_executor(repository, resources, fence, action.operation_id)
                observe('tool_committed', action.operation_id)
                continue
            if action.kind == 'resume_prepared':
                request = repository.resume_prepared(fence, action.attempt_id)
            else:
                request = repository.prepare_model(fence, context_builder(repository, resources, fence))
            observe('model_prepared', request.attempt_id)
            if stop_event.is_set():
                break
            # Refresh the deadline and source authority immediately before IO;
            # a prepared request may have survived a process outage.
            request = repository.resume_prepared(fence, request.attempt_id)
            repository.mark_model_sending(fence, request.attempt_id)
            observe('model_sending', request.attempt_id)
            log('model_request', request.attempt_id)
            started = monotonic()
            try:
                reply = collect_reply(model.stream(request))
            except ModelError as error:
                log('model_usage', request.attempt_id, elapsed=monotonic() - started, error=error.code)
                if error.code == 'configuration_unavailable':
                    return repository.pause_work(fence, error.code)
                repository.interrupt_model(fence, request.attempt_id, error.code)
                continue
            log('model_usage', request.attempt_id, elapsed=monotonic() - started, usage=reply.usage)
            # The ledger allows trusted usage settlement even if new user input
            # superseded this request. Business writes still require the fence.
            try:
                repository.commit_model(fence, request.attempt_id, reply)
            except HrAgentProblem as error:
                if error.problem['code'] == 'lease_lost':
                    repository.settle_usage(worker, request.attempt_id, uuid4(), reply.usage)
                raise
            observe('model_committed', request.attempt_id)
        except ContextRebuildRequired:
            continue
        except WorkPaused as paused:
            return paused.view
        except HrAgentProblem as error:
            if error.problem['code'] == 'lease_lost':
                return repository.worker_view(fence)
            if error.problem['code'] in {'configuration_unavailable', 'reference_unavailable', 'temporarily_unavailable', 'dependency_revoked', 'hash_mismatch', 'scope_denied', 'not_found'}:
                return repository.pause_work(fence, error.problem['code'])
            raise
    return repository.worker_view(fence)
