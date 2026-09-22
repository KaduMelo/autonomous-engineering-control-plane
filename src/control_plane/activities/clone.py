"""Obtain the repository named by a notification.

A pure function of `(source, revision)`. It returns the cache key, never a path:
other activities re-derive the directory from the same inputs, so nothing
filesystem-shaped ever crosses an activity boundary. Returning a path is exactly
what makes a resume on another worker fail.
"""

from __future__ import annotations

import asyncio

from temporalio import activity
from temporalio.exceptions import ApplicationError

from control_plane import config
from control_plane.adapters import repo
from control_plane.domain.models import CloneInput


async def _heartbeat_forever(source: str) -> None:
    elapsed = 0.0
    while True:
        await asyncio.sleep(config.HEARTBEAT_INTERVAL_S)
        elapsed += config.HEARTBEAT_INTERVAL_S
        activity.heartbeat({"source": source, "elapsed_s": elapsed})


@activity.defn
async def ensure_clone(request: CloneInput) -> str:
    """Return the cache key for a clone of `source` containing `revision`.

    Raises CloneError (retryable) when the source cannot be reached, and a
    non-retryable ApplicationError when the revision is absent after a successful
    clone and fetch - a force-push will not undo itself.
    """
    dest = repo.resolve_source(request.source)

    if dest != repo.cache_dir_for(request.source):
        # A local repository. Nothing to clone; just confirm the revision is there.
        if repo.resolve(dest, request.revision) is None:
            raise ApplicationError(
                f"revision {request.revision} is not in {request.source}",
                type="UnknownRevision",
                non_retryable=True,
            )
        return dest.name

    beat: asyncio.Task[None] | None = None
    if activity.in_activity():
        beat = asyncio.create_task(_heartbeat_forever(request.source))
    try:
        await asyncio.to_thread(repo.clone_or_fetch, request.source, dest)
    finally:
        if beat is not None:
            beat.cancel()

    if repo.resolve(dest, request.revision) is None:
        raise ApplicationError(
            f"revision {request.revision} is not in {request.source} after fetching",
            type="UnknownRevision",
            non_retryable=True,
        )
    return dest.name
