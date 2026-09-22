"""The result-vs-exception contract (SC-005, constitution Principle V).

A failing test suite is a *result*. A broken runner is an *error*. Collapsing
the two either burns retries on code that is simply wrong, or accepts
infrastructure failure as an agent verdict. Both directions are covered here.
"""

from __future__ import annotations

import pytest

from control_plane.adapters.sandbox import SandboxRun, verdict
from control_plane.domain.errors import TestRunnerError

FAILURE_OUTPUT = """\
=========================== short test summary info ============================
FAILED tests/test_pricing.py::test_apply_discount[200.0-10.0-180.0]
FAILED tests/test_pricing.py::test_apply_discount[50.0-25.0-37.5]
3 failed, 4 passed in 0.01s
"""


def _run(exit_code: int, output: str = "") -> SandboxRun:
    return SandboxRun(exit_code=exit_code, output=output, duration_s=0.5)


def test_exit_zero_is_a_passing_result():
    result = verdict(_run(0, "4 passed in 0.01s"))
    assert result.passed is True
    assert result.failing_tests == []


def test_exit_one_is_a_failing_result_not_an_error():
    result = verdict(_run(1, FAILURE_OUTPUT))
    assert result.passed is False
    assert result.output  # the failure output is what feeds the next proposal


def test_exit_one_parses_the_failing_node_ids():
    result = verdict(_run(1, FAILURE_OUTPUT))
    assert result.failing_tests == [
        "tests/test_pricing.py::test_apply_discount[200.0-10.0-180.0]",
        "tests/test_pricing.py::test_apply_discount[50.0-25.0-37.5]",
    ]


@pytest.mark.parametrize(
    ("exit_code", "why"),
    [
        (2, "pytest interrupted"),
        (3, "pytest internal error"),
        (4, "pytest usage error"),
        (5, "no tests collected - a patch may have deleted the test file"),
        (125, "docker itself failed"),
        (126, "command found but not executable"),
        (127, "command not found"),
        (137, "SIGKILL - OOM or the timeout kill"),
        (99, "unknown"),
    ],
)
def test_every_other_exit_code_is_an_infrastructure_error(exit_code, why):
    with pytest.raises(TestRunnerError):
        verdict(_run(exit_code, why))


def test_no_tests_collected_is_never_reported_as_a_red_suite():
    """Exit 5 is the trap: a deleted test file makes the suite 'not fail'."""
    with pytest.raises(TestRunnerError):
        verdict(_run(5, "no tests ran"))


def test_duration_is_carried_through():
    assert verdict(_run(0)).duration_s == 0.5
