"""Parsing a notification (T011, FR-003)."""

from __future__ import annotations

import ast
import inspect

import pytest
from pydantic import ValidationError

from control_plane.domain.models import BuildNotification
from control_plane.ingress import app as ingress_app

VALID = {
    "repository_url": "https://host.invalid/owner/name.git",
    "repository_full_name": "owner/name",
    "revision": "a" * 40,
    "conclusion": "failure",
}


def test_a_valid_notification_parses():
    assert BuildNotification(**VALID).failed is True


def test_a_successful_build_is_not_a_failure():
    assert BuildNotification(**{**VALID, "conclusion": "success"}).failed is False


@pytest.mark.parametrize("missing", list(VALID))
def test_every_field_is_required(missing):
    payload = {k: v for k, v in VALID.items() if k != missing}
    with pytest.raises(ValidationError):
        BuildNotification(**payload)


def test_the_body_is_verified_before_it_is_parsed():
    """Parsing is already acting on untrusted input.

    Checked structurally: inside the handler, the verify call must appear before
    any json parse.
    """
    source = inspect.getsource(ingress_app)
    tree = ast.parse(source)
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "webhook"
    )

    verify_line = min(
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "verify"
    )
    parse_lines = [
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"loads", "json"}
    ]
    assert parse_lines, "the handler must parse the body somewhere"
    assert verify_line < min(parse_lines), "verification must come first"
