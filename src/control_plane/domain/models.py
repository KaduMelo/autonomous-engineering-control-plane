"""Types that cross Temporal activity boundaries.

Two rules govern everything here, and both exist to make resume work:

1. No live handles. No open files, no clients, and - critically - no filesystem
   path that outlives the activity that produced it. This is why there is no
   `WorkspaceRef`: `apply_patch` and `run_tests` each rebuild their own
   workspace from a clean checkout, so nothing filesystem-shaped ever has to
   survive a worker dying.
2. Bounded size. Payloads land in the event history, which has limits, and an
   unbounded suite log would blow it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_OUTPUT_CHARS = 20_000
MAX_SOURCE_CHARS = 40_000


def truncate(text: str, limit: int) -> str:
    """Trim `text` to `limit`, marking the cut so nobody mistakes it for the whole thing."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return f"{text[:limit]}\n\n[... truncated, {dropped} characters dropped ...]"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class FixRequest(_Frozen):
    """What the operator hands over. Its revision determines the run's identity."""

    repo_path: str
    revision: str
    max_attempts: int = 3


class SourceFile(_Frozen):
    path: str
    content: str


class RepositoryContext(_Frozen):
    """What the fixer gets to see.

    `failing_tests` is discovered by the baseline validation run, never supplied
    by the operator.
    """

    failing_tests: list[str]
    failure_output: str
    sources: list[SourceFile]


class ProposedChange(_Frozen):
    """A candidate fix, expressed against the target revision so it can be re-applied."""

    diff: str
    rationale: str
    tree_hash: str | None = None


class TestResult(_Frozen):
    """A verdict, never an error.

    A broken runner does not produce one of these - it raises TestRunnerError.
    """

    passed: bool
    output: str
    failing_tests: list[str] = Field(default_factory=list)
    duration_s: float = 0.0


class Attempt(_Frozen):
    """One pass of propose -> apply -> validate."""

    ordinal: int
    change: ProposedChange
    applied: bool
    result: TestResult | None = None


class FixBranch(_Frozen):
    """The deliverable. `created` is False when the branch already carried this patch."""

    name: str
    commit: str
    created: bool


RunStatus = Literal["fixed", "exhausted", "conflicted"]


class RunOutcome(_Frozen):
    status: RunStatus
    attempts: list[Attempt] = Field(default_factory=list)
    winning_change: ProposedChange | None = None
    branch: FixBranch | None = None
