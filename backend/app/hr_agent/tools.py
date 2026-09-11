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
        receipt_validator = None
        if operation.name == "read_resource":
            from .context import ensure_read_result_fits_summary

            def receipt_validator(current, checkpoint, records, new_entry_id):
                for record in records:
                    ensure_read_result_fits_summary(
                        repository,
                        resources,
                        fence,
                        record["operation"],
                        record["payload"],
                        current=current,
                        checkpoint=checkpoint,
                        entry_id=record["entry_id"],
                        entry_seq=record["entry_seq"],
                        overflow_message=(
                            "read_resource cannot add this range until existing tool content is processed"
                            if record["entry_id"] != new_entry_id
                            else "read_resource range is too large for this work context; retry with a smaller limit"
                        ),
                    )

        return repository.commit_read(
            fence, operation_id, payload, receipt_validator=receipt_validator
        )
    except HrAgentProblem as error:
        if error.problem["code"] == "lease_lost" or operation.status == "committed":
            raise
        return repository.fail_tool(fence, operation_id, error)
