"""Every ceiling and every external identifier, in one place.

Defaults are written `os.environ.get(KEY) or default`, never
`os.environ.get(KEY, default)`. The second form returns "" for a variable that
is set but empty, so the default never applies - which is how CI, where the
variables are set to "" on purpose, found a failure the local suite could not.

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

TASK_QUEUE = os.environ.get("CONTROL_PLANE_TASK_QUEUE") or "fix-loop"
TEMPORAL_TARGET = os.environ.get("TEMPORAL_TARGET") or "localhost:7233"

# --- Sandbox ---
SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE") or "fixloop-sandbox:latest"
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
MODEL_ID = os.environ.get("CONTROL_PLANE_MODEL") or "claude-opus-5"
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
# Without a heartbeat, a worker dying mid-proposal is only noticed when
# PROPOSE_TIMEOUT expires - a five minute stall on every resume.
PROPOSE_HEARTBEAT = timedelta(seconds=30)
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
NON_RETRYABLE_ERROR_TYPES = (
    "PatchApplyError",
    "BranchConflictError",
    "UnknownRevision",
    "HostApiError",
)


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


# --- Ingress (feature 002) ---
INGRESS_HOST = os.environ.get("CONTROL_PLANE_INGRESS_HOST") or "127.0.0.1"
INGRESS_PORT = int(os.environ.get("CONTROL_PLANE_INGRESS_PORT") or "8080")
# Shared secret for the HMAC over the raw request body. Read at call time so a
# test can set it; see workspace_root() for why import-time binding is a trap.
WEBHOOK_SECRET_ENV = "CONTROL_PLANE_WEBHOOK_SECRET"


def webhook_secret() -> bytes:
    secret = os.environ.get(WEBHOOK_SECRET_ENV, "")
    return secret.encode()


# --- Repository host (feature 002) ---
# Points at the local stand-in by default. Nothing else about the adapter
# changes between offline and a real host.
HOST_API_BASE_URL = os.environ.get("CONTROL_PLANE_HOST_API") or "http://127.0.0.1:8099"
HOST_TOKEN_ENV = "CONTROL_PLANE_HOST_TOKEN"


def host_token() -> str:
    return os.environ.get(HOST_TOKEN_ENV) or "offline-stand-in-token"


def clone_cache_root() -> Path:
    """Where clones are cached, keyed by source.

    A function for the same reason as workspace_root(): resolving a path touches
    the filesystem, and this module is imported by workflow code.
    """
    override = os.environ.get("CONTROL_PLANE_CLONE_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / ".workspaces" / "clones"


CLONE_TIMEOUT = timedelta(seconds=300)
CLONE_HEARTBEAT = timedelta(seconds=30)
OPEN_PR_TIMEOUT = timedelta(seconds=120)
