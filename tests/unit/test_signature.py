"""The notification is untrusted until its signature verifies (T010, FR-002).

Three failure modes close here: a forged notification, a tampered one, and a
timing side channel. The third is why comparison must be constant-time.
"""

from __future__ import annotations

import ast
import inspect

from control_plane.ingress import signature

SECRET = b"shared-secret"
BODY = b'{"revision":"abc","conclusion":"failure"}'


def test_a_signature_it_produced_verifies():
    assert signature.verify(BODY, signature.sign(BODY, SECRET), SECRET) is True


def test_a_missing_header_does_not_verify():
    assert signature.verify(BODY, None, SECRET) is False
    assert signature.verify(BODY, "", SECRET) is False


def test_a_malformed_header_does_not_verify():
    for header in ("garbage", "sha256=", "sha1=abcd", "sha256=zz", "abcd"):
        assert signature.verify(BODY, header, SECRET) is False, header


def test_a_signature_from_a_different_secret_does_not_verify():
    assert signature.verify(BODY, signature.sign(BODY, b"other"), SECRET) is False


def test_a_tampered_body_does_not_verify():
    """The signature covers the body, so changing it invalidates the signature."""
    valid = signature.sign(BODY, SECRET)
    assert signature.verify(BODY + b" ", valid, SECRET) is False
    assert signature.verify(BODY.replace(b"failure", b"success"), valid, SECRET) is False


def test_an_empty_secret_never_verifies():
    """An unset secret must fail closed, not accept everything."""
    assert signature.verify(BODY, signature.sign(BODY, b""), b"") is False


def test_comparison_is_constant_time():
    """`==` on a digest leaks how much of it matched. Checked on the code."""
    tree = ast.parse(inspect.getsource(signature))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "compare_digest" in calls
    comparisons = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and any(isinstance(op, ast.Eq) for op in node.ops)
    ]
    assert not comparisons, "no digest may be compared with =="
