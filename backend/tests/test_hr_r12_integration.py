from __future__ import annotations

import ast
import inspect

from app.main import create_app


def test_create_app_declares_all_hr_r12_service_boundaries() -> None:
    parameters = set(inspect.signature(create_app).parameters)

    assert {
        "hr_position_intelligence_service",
        "hr_candidate_service",
        "hr_resource_service",
        "hr_task_context_provider",
        "hr_position_task_service",
    }.issubset(parameters)


def test_create_app_wires_candidate_documents_to_the_existing_download_service() -> None:
    tree = ast.parse(inspect.getsource(create_app))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CandidateService"
    ]

    assert len(calls) == 1
    assert {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in calls[0].keywords
    }["document_tickets"] == "conversation_attachment_download_service"
