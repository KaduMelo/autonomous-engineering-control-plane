"""What a reviewer must be able to read without opening anything else (T022, FR-011, SC-009)."""

from __future__ import annotations

from control_plane.activities.pull_request import render_body, render_title
from control_plane.domain.models import OpenPullRequestInput

REVISION = "4b1a0d431c4eda26a485496e3f50504dd49d7f2f"

REQUEST = OpenPullRequestInput(
    source="https://host.invalid/owner/name.git",
    repository_full_name="owner/name",
    revision=REVISION,
    branch=f"fix/{REVISION}",
    failing_tests=["tests/test_pricing.py::test_apply_discount"],
    winning_diff="--- a/src/calculator/pricing.py\n+++ b/src/calculator/pricing.py\n-old\n+new\n",
    rationale="percent is a percentage of the price",
    attempts=3,
)


def test_the_title_names_the_broken_commit():
    assert REVISION[:12] in render_title(REQUEST)


def test_the_body_names_the_failing_tests():
    assert "tests/test_pricing.py::test_apply_discount" in render_body(REQUEST)


def test_the_body_carries_the_winning_diff():
    """FR-018 lets the fixer edit tests, so the diff is the only real evidence."""
    body = render_body(REQUEST)
    assert "+new" in body
    assert "```diff" in body


def test_the_body_states_how_many_attempts_it_took():
    assert "3" in render_body(REQUEST)


def test_the_body_carries_the_reasoning():
    assert "percent is a percentage" in render_body(REQUEST)


def test_the_body_warns_that_green_is_not_proof():
    """The mitigation FR-018 requires, placed where the reviewer actually looks."""
    body = render_body(REQUEST).lower()
    assert "test" in body and ("weaken" in body or "not proof" in body or "review" in body)


def test_a_run_with_no_diff_still_renders():
    empty = REQUEST.model_copy(update={"winning_diff": "", "failing_tests": []})
    body = render_body(empty)
    assert body.strip()
