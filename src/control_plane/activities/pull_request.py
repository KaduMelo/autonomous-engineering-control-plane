"""Publish the fix branch and open exactly one pull request.

Idempotent by two independent guards: ask before creating, and treat the host's
"already exists" as success. The second is what makes the property hold under
retries that interleave rather than merely repeat.
"""

from __future__ import annotations

import asyncio

from temporalio import activity
from temporalio.exceptions import ApplicationError

from control_plane.adapters import repo
from control_plane.adapters.host import GitHubHost, RepositoryHostPort
from control_plane.domain.errors import HostApiError
from control_plane.domain.models import OpenPullRequestInput, PullRequestRef, truncate

MAX_DIFF_IN_BODY = 12_000

_host: RepositoryHostPort | None = None


def set_host(host: RepositoryHostPort | None) -> None:
    """Swap the host. Tests and the offline demo point this at the stand-in."""
    global _host
    _host = host


def _get_host() -> RepositoryHostPort:
    global _host
    if _host is None:
        # Constructed here, inside the activity, so the token never reaches
        # workflow code or the sandbox.
        _host = GitHubHost()
    return _host


def render_title(request: OpenPullRequestInput) -> str:
    return f"fix: failing tests at {request.revision[:12]}"


def render_body(request: OpenPullRequestInput) -> str:
    """What a reviewer needs without opening anything else (FR-011, SC-009)."""
    tests = "\n".join(f"- `{node}`" for node in request.failing_tests) or "_none reported_"
    diff = (
        f"```diff\n{truncate(request.winning_diff, MAX_DIFF_IN_BODY)}\n```"
        if request.winning_diff
        else "_no diff recorded_"
    )
    return f"""\
Automated fix for `{request.revision}`.

## What was failing

{tests}

## Why this change

{request.rationale or "_no rationale recorded_"}

## The change

{diff}

## How it got here

Reached a green suite on attempt **{request.attempts}**, validated in an isolated
container with no network.

> The agent is allowed to edit test files, so a green suite is **not proof on its
> own** — a deleted test is also green. Read the diff above before approving.
"""


@activity.defn
async def open_pr(request: OpenPullRequestInput) -> PullRequestRef:
    """Push the branch, then ensure exactly one pull request exists for it."""
    source = repo.resolve_source(request.source)

    try:
        await asyncio.to_thread(repo.push_ref, source, "origin", request.branch)
    except Exception as exc:  # git failures are infrastructure: retry them
        raise RuntimeError(f"could not push {request.branch}: {exc}") from exc

    host = _get_host()
    try:
        existing = await host.find_open_pull_request(request.repository_full_name, request.branch)
        if existing is not None:
            return existing

        return await host.create_pull_request(
            request.repository_full_name,
            request.branch,
            "main",
            render_title(request),
            render_body(request),
        )
    except HostApiError as exc:
        if exc.retryable:
            raise
        raise ApplicationError(str(exc), type="HostApiError", non_retryable=True) from exc
