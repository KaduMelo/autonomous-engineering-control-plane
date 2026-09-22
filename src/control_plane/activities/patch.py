"""Verify a diff applies, and say what tree it produces.

A pure function of (revision, diff): it builds a throwaway checkout, applies the
diff there, records the resulting tree hash and throws the checkout away. It
returns no path, which is why a retry - or a resume on another worker - behaves
exactly like a first run.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from temporalio import activity

from control_plane.adapters import repo
from control_plane.domain.models import ApplyInput, ProposedChange


@activity.defn
async def apply_patch(request: ApplyInput) -> ProposedChange:
    """Raises PatchApplyError when the diff is malformed or does not apply."""
    workspace = Path(tempfile.mkdtemp(prefix="apply-"))
    try:
        repo.materialize(request.repo_path, request.revision, workspace)
        repo.apply_diff(workspace, request.diff)
        return ProposedChange(
            diff=request.diff,
            rationale="",
            tree_hash=repo.tree_hash(request.repo_path, workspace),
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
