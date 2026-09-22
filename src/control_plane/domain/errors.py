"""Failures that carry meaning for the retry policy.

The distinction that matters most is between a test suite that fails (a
TestResult, returned normally) and a runner that broke (TestRunnerError,
raised). Collapsing the two either burns retries on code that is simply wrong,
or silently accepts infrastructure failure as an agent verdict.
"""

from __future__ import annotations


class ControlPlaneError(Exception):
    """Base for every error this system raises deliberately."""


class TestRunnerError(ControlPlaneError):
    """The validation environment broke - not a failing test.

    Raised for every container exit code that is neither 0 (passed) nor 1
    (tests failed): pytest internal errors, no tests collected, docker failures,
    and SIGKILL from the timeout. Retryable: the runner may work next time.
    """

    __test__ = False


class PatchApplyError(ControlPlaneError):
    """The proposed diff is malformed or does not apply to the target revision.

    Not retryable - the same diff will not apply next time either. The workflow
    catches this, spends the attempt, and continues.
    """


class BranchConflictError(ControlPlaneError):
    """The fix branch exists and carries different content.

    Not retryable, and deliberately not overwritten: a control plane that
    silently replaces a previous result is the failure this guards against.
    """


class CloneError(ControlPlaneError):
    """The repository could not be cloned or fetched.

    Retryable: network, transient auth, or a host that was briefly unreachable.
    A revision that is missing *after* a successful clone is a different thing -
    a force-push will not undo itself - and is raised as non-retryable instead.
    """


class HostApiError(ControlPlaneError):
    """The repository host refused or failed.

    Carries whether retrying could help, because only the adapter knows: a 429
    or a 502 is worth another attempt, a 401 or a missing repository is not.
    The activity turns `retryable=False` into a non-retryable ApplicationError
    rather than letting Temporal burn the budget on a certainty.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
