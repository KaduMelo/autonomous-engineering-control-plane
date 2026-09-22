"""Ask the fixer for a change, and classify what can go wrong.

The classification is the point. A 429 or a 5xx is worth retrying; a malformed
request or a bad credential is not, and retrying it only burns the budget.
"""

from __future__ import annotations

import anthropic
from temporalio import activity
from temporalio.exceptions import ApplicationError

from control_plane.adapters.fixer import AnthropicFixer, FixerPort
from control_plane.domain.models import ProposedChange, ProposeInput

# Retrying these cannot change the answer.
NON_RETRYABLE = (
    anthropic.BadRequestError,
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
)

_fixer: FixerPort | None = None


def set_fixer(fixer: FixerPort | None) -> None:
    """Swap the proposer. Used by tests to drive the loop without an API key."""
    global _fixer
    _fixer = fixer


def _get_fixer() -> FixerPort:
    global _fixer
    if _fixer is None:
        # Constructed here, inside the activity, so the credential never reaches
        # workflow code or the sandbox.
        _fixer = AnthropicFixer()
    return _fixer


@activity.defn
async def propose_fix(request: ProposeInput) -> ProposedChange:
    try:
        proposal = await _get_fixer().propose(request.context, request.history)
    except NON_RETRYABLE as exc:
        raise ApplicationError(
            f"{type(exc).__name__}: {exc}",
            type=type(exc).__name__,
            non_retryable=True,
        ) from exc
    # Everything else - RateLimitError, InternalServerError, APIConnectionError,
    # APITimeoutError - propagates so Temporal retries with backoff.
    return ProposedChange(diff=proposal.diff, rationale=proposal.rationale)
