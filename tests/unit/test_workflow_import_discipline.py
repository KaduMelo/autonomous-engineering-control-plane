"""Workflow code must stay deterministic by construction (Principle IV).

Ruff cannot express "this directory may not import that one", so the rule is a
test - which is better anyway, because it runs in CI rather than only at lint
time. It checks the source with the ast module rather than importing anything,
so a violation is reported even if the import would fail.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / "src" / "control_plane" / "workflows"

# Importing any of these into workflow code makes replay unsafe.
BANNED_PREFIXES = (
    "control_plane.adapters",
    "subprocess",
    "socket",
    "random",
    "secrets",
    "uuid",
    "requests",
    "httpx",
    "anthropic",
    "docker",
)

# Calls that read the clock or the machine, rather than the replayed history.
BANNED_CALLS = {
    "datetime.now",
    "datetime.utcnow",
    "time.time",
    "time.monotonic",
    "os.getenv",
    "os.urandom",
    "random.random",
    "uuid.uuid4",
}


def workflow_modules() -> list[Path]:
    return [p for p in WORKFLOWS.rglob("*.py") if p.name != "__init__.py"]


def test_there_is_workflow_code_to_check():
    """Guards against this whole file silently passing on an empty directory."""
    assert workflow_modules()


@pytest.mark.parametrize("module", workflow_modules(), ids=lambda p: p.name)
def test_workflow_modules_import_nothing_that_breaks_replay(module: Path):
    tree = ast.parse(module.read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    offenders = [name for name in imported if name.startswith(BANNED_PREFIXES)]
    assert not offenders, (
        f"{module.name} imports {offenders}; workflow code must reach the outside "
        "world only through activities"
    )


@pytest.mark.parametrize("module", workflow_modules(), ids=lambda p: p.name)
def test_workflow_modules_do_not_read_the_clock_or_the_machine(module: Path):
    tree = ast.parse(module.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            if isinstance(value, ast.Name):
                dotted = f"{value.id}.{node.func.attr}"
                if dotted in BANNED_CALLS:
                    offenders.append(dotted)
    assert not offenders, (
        f"{module.name} calls {offenders}; use workflow.now() or move it into an activity"
    )


@pytest.mark.parametrize("module", workflow_modules(), ids=lambda p: p.name)
def test_activity_and_domain_imports_are_marked_pass_through(module: Path):
    """Imports of our own code must sit inside imports_passed_through()."""
    source = module.read_text()
    if "control_plane." not in source:
        return
    assert "imports_passed_through" in source, (
        f"{module.name} imports control_plane modules outside "
        "workflow.unsafe.imports_passed_through()"
    )
