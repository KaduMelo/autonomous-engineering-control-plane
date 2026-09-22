"""Every ceiling and every external identifier, in one place.

Timeouts are laid out so the inner limit always fires before the outer one -
that is what keeps a failure attributable to the thing that actually failed
rather than to Temporal noticing late.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from temporalio.common import RetryPolicy

TASK_QUEUE = os.environ.get("CONTROL_PLANE_TASK_QUEUE", "fix-loop")
TEMPORAL_TARGET = os.environ.get("TEMPORAL_TARGET", "localhost:7233")

# --- Sandbox ---
SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "fixloop-sandbox:latest")
SANDBOX_MEMORY = "512m"
SANDBOX_PIDS_LIMIT = 256
SANDBOX_CPUS = "2.0"
SANDBOX_UID = 10001
# The container is killed at this point. It must stay below RUN_TESTS_TIMEOUT so
# the inner limit fires first and the failure is attributable to the suite.
SANDBOX_WALL_CLOCK_S = 300.0


def workspace_root() -> Path:
    """Where throwaway workspaces are built.

    A function, not a module constant: resolving a path touches the filesystem,
    and this module is imported by workflow code, where the Temporal sandbox
    (correctly) forbids that. Computing it lazily also means the environment
    variable is honoured at call time rather than frozen at import - the same
    trap that made SANDBOX_IMAGE impossible to override.
    """
    override = os.environ.get("CONTROL_PLANE_WORKSPACE_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / ".workspaces"


# --- Fix proposer ---
MODEL_ID = os.environ.get("CONTROL_PLANE_MODEL", "claude-opus-5")
MODEL_MAX_TOKENS = 16_000
# Typed as the Literal the SDK expects, so a typo is a type error rather than
# a 400 at run time.
MODEL_EFFORT: Literal["low", "medium", "high", "xhigh", "max"] = "high"
# Below RUN_PROPOSE_TIMEOUT, so the SDK raises a clean APITimeoutError before
# Temporal's own timeout fires.
MODEL_TIMEOUT_S = 240.0

# --- Activity ceilings ---
RUN_TESTS_TIMEOUT = timedelta(seconds=600)
RUN_TESTS_HEARTBEAT = timedelta(seconds=30)
HEARTBEAT_INTERVAL_S = 5.0
PROPOSE_TIMEOUT = timedelta(seconds=300)
ANALYZE_TIMEOUT = timedelta(seconds=60)
APPLY_TIMEOUT = timedelta(seconds=60)
BRANCH_TIMEOUT = timedelta(seconds=60)

# --- Loop ---
DEFAULT_MAX_ATTEMPTS = 3
BRANCH_PREFIX = "fix/"

# --- Retry policy (the only retry layer in the system) ---
RETRY_INITIAL_INTERVAL = timedelta(seconds=1)
RETRY_BACKOFF_COEFFICIENT = 2.0
RETRY_MAXIMUM_INTERVAL = timedelta(seconds=60)
RETRY_MAXIMUM_ATTEMPTS = 3
# Retrying these cannot change the outcome, so they fail the activity at once.
NON_RETRYABLE_ERROR_TYPES = ("PatchApplyError", "BranchConflictError")


def retry_policy() -> RetryPolicy:
    """The system's only retry layer.

    Temporal's default is unlimited attempts with backoff, which would make the
    attempt budget meaningless - a single wedged activity could retry forever.
    `non_retryable_error_types` covers the two failures a second attempt cannot
    change: a diff that will not apply, and a branch already carrying different
    content.
    """
    from temporalio.common import RetryPolicy

    return RetryPolicy(
        initial_interval=RETRY_INITIAL_INTERVAL,
        backoff_coefficient=RETRY_BACKOFF_COEFFICIENT,
        maximum_interval=RETRY_MAXIMUM_INTERVAL,
        maximum_attempts=RETRY_MAXIMUM_ATTEMPTS,
        non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES),
    )
