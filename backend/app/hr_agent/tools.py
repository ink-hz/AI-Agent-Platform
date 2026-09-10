"""Execute only persistent model slots; no confirmation, HTTP or shell tool."""

from .types import HrAgentProblem, problem, validate_tool_arguments


def execute_tool(repository, resources, fence, operation_id):
    operation = repository.load_operation(fence, operation_id)
    try:
        arguments = validate_tool_arguments(operation.name, operation.arguments)
        _current, _record, view, _owner = repository.context_input(fence)
        repository.validate_operation_dependencies(fence, operation_id)
        if operation.status == "committed":
            return operation.receipt
        if view["phase"] == "finalizing" and operation.name not in (
            "save_note",
            "save_result",
            "ask_user",
        ):
            raise problem("scope_denied", http_status=403)
        if operation.name in ("save_note", "save_result", "ask_user"):
            return repository.execute_local_tool(fence, operation_id)
        payload = (
            resources.list_resources(fence, arguments)
            if operation.name == "list_resources"
            else resources.read_resource(fence, arguments)
        )
        return repository.commit_read(fence, operation_id, payload)
    except HrAgentProblem as error:
        if error.problem["code"] == "lease_lost" or operation.status == "committed":
            raise
        return repository.fail_tool(fence, operation_id, error)
