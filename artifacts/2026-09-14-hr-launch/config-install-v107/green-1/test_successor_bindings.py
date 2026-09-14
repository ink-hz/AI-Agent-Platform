"""Successors remain inert until immutable identities are supplied by root."""

import ast
import importlib.util
from pathlib import Path
import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
BUILD = HERE / "stage_build_v4.py"
INSTALL = HERE.parent / "config-install-v107/incremental_install_v107.py"


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("path", [BUILD, INSTALL])
def test_unbound_identity_fails_before_any_external_operation(
    path, tmp_path, monkeypatch
):
    m = load(path)

    def external(*args, **kwargs):
        raise AssertionError("external operation before binding")

    monkeypatch.setattr(m.subprocess, "run", external)
    if path == BUILD:
        m.RELEASE_SHA = "UNBOUND_RELEASE_SHA"
        with pytest.raises(RuntimeError, match="release_identity_unbound"):
            m.start(True)
        with pytest.raises(RuntimeError, match="release_identity_unbound"):
            m.build_source_package(tmp_path / "absent")
        assert not (tmp_path / "absent").exists()
    else:
        m.RELEASE = "UNBOUND_RELEASE_SHA"

        class Host:
            call = external

        with pytest.raises(m.InstallError, match="release_identity_unbound"):
            m.install(Host(), "a" * 32)


def normalized(path):
    tree = ast.parse(path.read_text())
    assignments = {
        "ARTIFACT_ROOT",
        "RUNS_ROOT",
        "RELEASE_SHA",
        "EXPECTED_SOURCE_TREE",
        "RELEASE",
        "IMAGE",
        "NEW_RUNTIME_SHA",
    }

    class Normalize(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            return None if node.name == "require_binding" else self.generic_visit(node)

        def visit_Assign(self, node):
            if any(
                isinstance(t, ast.Name) and t.id in assignments for t in node.targets
            ):
                node.value = ast.Constant("IDENTITY")
            return self.generic_visit(node)

        def visit_Expr(self, node):
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "require_binding"
            ):
                return None
            return self.generic_visit(node)

        def visit_If(self, node):
            if (
                len(node.body) == 1
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Call)
                and isinstance(node.body[0].value.func, ast.Name)
                and node.body[0].value.func.id == "require_binding"
            ):
                return None
            return self.generic_visit(node)

        def visit_Call(self, node):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "protected"
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "OLD_RUNTIME"
            ):
                node.args[1] = ast.Constant("REVIEWED_ROOT_OWNER_CORRECTION")
            return self.generic_visit(node)

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                node.value = (
                    node.value.replace(
                        "394c9ecd96a292db4cd6c4a0ea0066a3acc3cd76", "RELEASE"
                    )
                    .replace("UNBOUND_RELEASE_SHA", "RELEASE")
                    .replace("1f12b4294ea209a7ebc70d0a5428e74dd8181971", "RELEASE")
                    .replace("stage_build_v3.py", "BUILD_SCRIPT")
                    .replace("stage_build_v4.py", "BUILD_SCRIPT")
                )
            return node

    return ast.dump(Normalize().visit(tree), include_attributes=False)


def test_normalized_successors_preserve_reviewed_implementation():
    assert normalized(BUILD) == normalized(
        ROOT / "artifacts/2026-09-13-hr-launch/production/stage_build_v3.py"
    )
    assert normalized(INSTALL) == normalized(
        HERE.parent / "config-install/incremental_install.py"
    )
