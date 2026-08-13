from __future__ import annotations

import pytest


def test_closure_contains_self_and_all_ancestors() -> None:
    from app.control_plane.directory import build_department_closure

    closure = build_department_closure({1: None, 2: 1, 3: 2, 4: 1})
    assert closure == (
        (1, 1, 0),
        (1, 2, 1),
        (1, 3, 2),
        (1, 4, 1),
        (2, 2, 0),
        (2, 3, 1),
        (3, 3, 0),
        (4, 4, 0),
    )


def test_closure_rejects_cycle_or_orphan() -> None:
    from app.control_plane.directory import DirectoryReconciliationError, build_department_closure

    with pytest.raises(DirectoryReconciliationError, match="department_cycle"):
        build_department_closure({1: 2, 2: 1})
    with pytest.raises(DirectoryReconciliationError, match="department_orphan"):
        build_department_closure({1: None, 2: 99})


def test_subtree_move_and_delete_rebuild_only_from_new_generation() -> None:
    from app.control_plane.directory import build_department_closure

    old = build_department_closure({1: None, 2: 1, 3: 2, 4: 1})
    new = build_department_closure({1: None, 2: 1, 3: 4, 4: 1})
    deleted = build_department_closure({1: None, 4: 1})
    assert (2, 3, 1) in old and (2, 3, 1) not in new
    assert (4, 3, 1) in new
    assert all(2 not in row and 3 not in row for row in deleted)


def test_member_can_belong_to_multiple_departments() -> None:
    from app.control_plane.directory import normalize_member_departments

    assert normalize_member_departments((3, 2, 3), {1, 2, 3}) == (2, 3)
    with pytest.raises(ValueError, match="member department invalid"):
        normalize_member_departments((2, 99), {1, 2, 3})
