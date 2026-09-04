from __future__ import annotations

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
