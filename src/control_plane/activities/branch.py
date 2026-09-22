"""Write the fix branch, or refuse to.

Deterministic name, check-if-exists, never overwrite. The ref is written with
plumbing, so the operator's HEAD, index and working tree do not move.
"""

from __future__ import annotations

from temporalio import activity

from control_plane import config
from control_plane.adapters import repo
from control_plane.domain.errors import BranchConflictError
from control_plane.domain.models import BranchInput, FixBranch


@activity.defn
async def create_fix_branch(request: BranchInput) -> FixBranch:
    """Ensure fix/<revision> exists carrying the winning patch.

    Absent -> created. Present with the same tree -> no-op success, which is the
    idempotency guarantee a retry or a resume lands on. Present with a different
    tree -> BranchConflictError, and the earlier result is left alone.
    """
    name = f"{config.BRANCH_PREFIX}{request.revision}"
    tree = request.change.tree_hash
    if tree is None:
        raise ValueError("the change has no tree hash - apply_patch must run first")

    existing = repo.resolve(request.repo_path, name)
    if existing is not None:
        if repo.tree_of(request.repo_path, existing) == tree:
            return FixBranch(name=name, commit=existing, created=False)
        raise BranchConflictError(
            f"{name} already exists carrying different content ({existing[:12]}); "
            "refusing to overwrite a previous result"
        )

    commit = repo.commit_tree(
        request.repo_path,
        tree,
        request.revision,
        f"fix: automated fix for {request.revision[:12]}\n\n{request.change.rationale}".strip(),
    )
    repo.update_ref(request.repo_path, f"refs/heads/{name}", commit)
    return FixBranch(name=name, commit=commit, created=True)
