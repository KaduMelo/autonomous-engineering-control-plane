"""The fix proposer, behind a port.

The workflow never sees a vendor. What it sees is `FixerPort`, which is what
lets the test suite drive the whole loop with a scripted fixer and no API key.
"""

from __future__ import annotations

from typing import Protocol

from anthropic import AsyncAnthropic
from anthropic.types import OutputConfigParam
from pydantic import BaseModel, Field

from control_plane import config
from control_plane.domain.models import Attempt, RepositoryContext, truncate


class FixProposal(BaseModel):
    """Structured output the proposer must return.

    Schema mirrored in specs/001-durable-fix-loop/contracts/fix_proposal.schema.json.
    """

    diff: str = Field(
        description=(
            "A unified diff against the target revision, applicable with `git apply` "
            "from a clean checkout."
        )
    )
    rationale: str = Field(
        description="Why this change makes the failing tests pass.", max_length=2000
    )
    files_touched: list[str] = Field(
        description="Repository-relative paths the diff modifies.", min_length=1
    )


class FixerPort(Protocol):
    """What the propose_fix activity depends on."""

    async def propose(self, context: RepositoryContext, history: list[Attempt]) -> FixProposal: ...


SYSTEM_PROMPT = """\
You fix failing tests in a Python repository.

You will be given the failing test node ids, the captured failure output, and the
source of the relevant files at a specific revision. Return a unified diff against
that revision which makes the suite pass.

Rules for the diff:
- It must apply cleanly with `git apply` from a pristine checkout of the revision.
- Use `--- a/<path>` and `+++ b/<path>` headers with repository-relative paths.
- Include accurate hunk headers and at least three lines of context.
- Change as little as possible.

You may edit any file, including tests. Note that a human reviews your diff, so a
change that makes the suite pass by weakening a test rather than fixing the defect
will be visible and is not what is wanted.
"""


def build_prompt(context: RepositoryContext, history: list[Attempt]) -> str:
    """Assemble the user message.

    Previous attempts come last and carry their failure output - that is the
    objective feedback the loop converges on (FR-007).
    """
    parts = [
        "## Failing tests",
        "\n".join(f"- {node}" for node in context.failing_tests) or "- (none reported)",
        "\n## Failure output",
        "```\n" + context.failure_output + "\n```",
        "\n## Source",
    ]
    for source in context.sources:
        parts.append(f"\n### {source.path}\n```python\n{source.content}\n```")

    if history:
        parts.append("\n## Previous attempts in this run - do not repeat them")
        for attempt in history:
            outcome = (
                "the diff did not apply"
                if not attempt.applied
                else "the suite still failed:\n```\n"
                + truncate(attempt.result.output if attempt.result else "", 4000)
                + "\n```"
            )
            parts.append(
                f"\n### Attempt {attempt.ordinal}\n"
                f"Rationale was: {attempt.change.rationale}\n"
                f"Diff was:\n```diff\n{attempt.change.diff}\n```\n"
                f"Outcome: {outcome}"
            )

    return "\n".join(parts)


class AnthropicFixer:
    """Claude, with its own retry layer switched off.

    `max_retries=0` is deliberate and load-bearing. Temporal already retries this
    activity; leaving the SDK's default of 2 in place would mean three Temporal
    attempts could hide nine billed calls behind three history entries, and the
    history is the evidence SC-003 measures. The client timeout sits below the
    activity's start-to-close so the SDK raises a clean APITimeoutError first.
    """

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        self._client = client or AsyncAnthropic(
            max_retries=0,
            timeout=config.MODEL_TIMEOUT_S,
        )

    async def propose(self, context: RepositoryContext, history: list[Attempt]) -> FixProposal:
        response = await self._client.messages.parse(
            model=config.MODEL_ID,
            max_tokens=config.MODEL_MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config=OutputConfigParam(effort=config.MODEL_EFFORT),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(context, history)}],
            output_format=FixProposal,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise ValueError(
                f"the proposer returned no parsable output (stop: {response.stop_reason})"
            )
        return parsed
