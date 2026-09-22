"""Every notification leaves a trace (T031, FR-015, SC-008).

The ingress is the one component whose failures are invisible in the run
history: a rejected notification produces no run at all. If an outcome is not
recorded here, it is not recorded anywhere.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from control_plane.domain.models import IngressOutcome
from control_plane.ingress import app as ingress_app


def test_every_decision_has_a_status():
    assert set(ingress_app.STATUS) == {"started", "deduplicated", "rejected", "failed"}


@pytest.mark.parametrize(
    ("decision", "status"),
    [("started", 202), ("deduplicated", 200), ("rejected", 200), ("failed", 503)],
)
def test_the_default_status_for_each_decision(decision, status):
    assert ingress_app.STATUS[decision] == status


def test_an_explicit_status_overrides_the_default():
    """401 and 400 are rejections too, and a sender must tell them apart."""
    response = ingress_app.record(
        IngressOutcome(decision="rejected", reason="bad signature"), status=401
    )
    assert response.status_code == 401


def test_a_recorded_outcome_never_serializes_empty_fields():
    response = ingress_app.record(IngressOutcome(decision="started", revision="a" * 40))
    assert b"null" not in response.body


def _handler() -> ast.AsyncFunctionDef:
    tree = ast.parse(inspect.getsource(ingress_app))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "webhook"
    )


def test_no_path_out_of_the_handler_escapes_being_recorded():
    """Structural, because a missed path is exactly what would go unnoticed."""
    returns = [node for node in ast.walk(_handler()) if isinstance(node, ast.Return)]
    assert returns, "the handler must return something"

    for node in returns:
        assert isinstance(node.value, ast.Call), f"line {node.lineno} returns without recording"
        assert getattr(node.value.func, "id", None) == "record", (
            f"line {node.lineno} returns without going through record()"
        )


def test_every_rejection_and_failure_carries_a_reason():
    """A rejection with no reason is an outcome nobody can act on."""
    for node in ast.walk(_handler()):
        if not isinstance(node, ast.Call) or getattr(node.func, "id", None) != "IngressOutcome":
            continue
        kwargs = {kw.arg: kw for kw in node.keywords}
        decision = kwargs.get("decision")
        if decision is None or not isinstance(decision.value, ast.Constant):
            continue
        if decision.value.value in {"rejected", "failed"}:
            assert "reason" in kwargs, (
                f"line {node.lineno}: a {decision.value.value} outcome must say why"
            )
