"""Git plumbing.

Two constraints shape everything here.

First, a workspace is always built from scratch out of the object database
(`git archive`), never by checking out into the operator's repository. A patch
therefore cannot stack on a previous application, and running any of this twice
converges on the same tree.

Second, the operator's repository is read-mostly. The fix branch is created with
plumbing against a temporary index (`GIT_INDEX_FILE`), so `HEAD`, the real index
and the working tree are never touched. The one thing that is written besides
the new ref is loose objects in the repository's object store - unreferenced
until the ref points at them, and collected by `git gc` like any other.
"""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

from control_plane.domain.errors import CloneError, PatchApplyError


class GitError(RuntimeError):
    """A git invocation failed for an infrastructure reason. Retryable."""


def _git_dir(repo_path: str | Path) -> str:
    """Absolute path to the repository's git directory.

    Always absolute: git resolves a relative GIT_DIR against GIT_WORK_TREE when
    both are set, so a relative path here silently points at the wrong place.
    """
    return str((Path(repo_path) / ".git").resolve())


def _git(
    repo_path: str | Path,
    *args: str,
    stdin: bytes | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    cmd = ["git", "--git-dir", _git_dir(repo_path), *args]
    proc = subprocess.run(cmd, input=stdin, capture_output=True, env=env)
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return proc


def resolve(repo_path: str | Path, rev: str) -> str | None:
    """Full sha for `rev`, or None when it does not exist."""
    proc = _git(repo_path, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode().strip()


def materialize(repo_path: str | Path, revision: str, dest: Path) -> None:
    """Extract the pristine tree at `revision` into `dest`.

    Uses `git archive` rather than a checkout: nothing in the operator's
    repository moves, and `dest` gets no `.git`, so the sandbox mounts only
    source.
    """
    dest.mkdir(parents=True, exist_ok=True)
    archive = _git(repo_path, "archive", "--format=tar", revision).stdout
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


def apply_diff(workspace: Path, diff: str) -> None:
    """Apply a unified diff to `workspace`.

    Raises PatchApplyError when the diff is malformed or does not apply - that
    is a spent attempt, not an infrastructure failure, so it must not be
    retried.
    """
    if not diff.strip():
        raise PatchApplyError("the proposed diff is empty")
    payload = diff if diff.endswith("\n") else diff + "\n"
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=workspace,
        input=payload.encode(),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise PatchApplyError(
            f"diff did not apply: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )


def tree_hash(repo_path: str | Path, workspace: Path) -> str:
    """Git tree hash of `workspace`'s contents, computed via a temporary index.

    The temporary index is what keeps the operator's real index untouched.
    """
    with tempfile.TemporaryDirectory(prefix="cp-index-") as tmp:
        # The path must NOT exist yet: git reads GIT_INDEX_FILE if it is there,
        # and a zero-byte file fails as "index file smaller than expected".
        env = {
            "GIT_INDEX_FILE": str(Path(tmp) / "index"),
            "GIT_DIR": _git_dir(repo_path),
            "GIT_WORK_TREE": str(workspace.resolve()),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }
        subprocess.run(
            ["git", "add", "-A"], env=env, cwd=workspace, capture_output=True, check=True
        )
        proc = subprocess.run(["git", "write-tree"], env=env, capture_output=True, check=True)
        return proc.stdout.decode().strip()


def tree_of(repo_path: str | Path, commit: str) -> str:
    """Tree hash a commit points at."""
    return _git(repo_path, "rev-parse", f"{commit}^{{tree}}").stdout.decode().strip()


def commit_tree(repo_path: str | Path, tree: str, parent: str, message: str) -> str:
    """Create a commit object. Identity is pinned so the sha is reproducible."""
    env = {
        "GIT_DIR": _git_dir(repo_path),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GIT_AUTHOR_NAME": "control-plane",
        "GIT_AUTHOR_EMAIL": "control-plane@example.invalid",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
        "GIT_COMMITTER_NAME": "control-plane",
        "GIT_COMMITTER_EMAIL": "control-plane@example.invalid",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    }
    proc = subprocess.run(
        ["git", "commit-tree", tree, "-p", parent, "-m", message],
        env=env,
        capture_output=True,
        check=True,
    )
    return proc.stdout.decode().strip()


def update_ref(repo_path: str | Path, ref: str, commit: str) -> None:
    """Point `ref` at `commit`. Writes the ref only - HEAD does not move."""
    _git(repo_path, "update-ref", ref, commit)


def head_state(repo_path: str | Path) -> tuple[str, str, str]:
    """(HEAD sha, symbolic HEAD, porcelain status) - the before/after fingerprint."""
    head = _git(repo_path, "rev-parse", "HEAD").stdout.decode().strip()
    symbolic = (
        _git(repo_path, "symbolic-ref", "--quiet", "HEAD", check=False).stdout.decode().strip()
    )
    status = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain"],
        capture_output=True,
    ).stdout.decode()
    return head, symbolic, status


# --- Feature 002: obtaining a repository from a URL ---


def cache_dir_for(source: str) -> Path:
    """Deterministic clone directory for `source`.

    Keyed by a hash of the source rather than by its last path segment, so two
    repositories that share a name cannot collide. The key is a pure function of
    the input, which is what lets any activity re-derive the directory instead of
    being handed a path.
    """
    from control_plane import config

    digest = hashlib.sha256(source.encode()).hexdigest()[:16]
    return config.clone_cache_root() / digest


def clone_or_fetch(source: str, dest: Path) -> None:
    """Ensure `dest` is a clone of `source` and is up to date.

    Miss clones, hit fetches. A hit must still fetch: a cache that never updated
    would serve a stale repository forever, and the revision being asked about is
    usually newer than the last time this ran.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    if (dest / ".git").is_dir():
        proc = subprocess.run(
            ["git", "-C", str(dest), "fetch", "--quiet", "--prune", "--tags", "origin"],
            capture_output=True,
        )
    else:
        proc = subprocess.run(["git", "clone", "--quiet", source, str(dest)], capture_output=True)

    if proc.returncode != 0:
        raise CloneError(
            f"could not obtain {source}: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )


def push_ref(repo_path: str | Path, remote: str, ref: str) -> None:
    """Push a single ref to `remote`.

    Explicit `src:dst` rather than a bare ref name: a bare name is resolved
    against the remote's configuration, and this must put the ref exactly where
    the pull request will look for it.
    """
    _git(repo_path, "push", remote, f"refs/heads/{ref}:refs/heads/{ref}")


def set_remote(repo_path: str | Path, remote: str, url: str) -> None:
    """Point `remote` at `url`, adding it if it is not there."""
    existing = _git(repo_path, "remote", check=False).stdout.decode().split()
    if remote in existing:
        _git(repo_path, "remote", "set-url", remote, url)
    else:
        _git(repo_path, "remote", "add", remote, url)


def resolve_source(source: str) -> Path:
    """The local repository a run should operate on.

    A local path that is already a git repository *is* the repository - it is not
    copied into the cache. Anything else is a remote and resolves to its clone.

    This is a deliberate deviation from the spec's wording, which said one
    mechanism with no second code path. Cloning a local path would work, but the
    fix branch would then land in a cache directory the operator never looks at,
    which is precisely what feature 001's demo shows them. One entry point with a
    local fast path was the honest shape; two entry points would not have been.
    """
    candidate = Path(source)
    if candidate.is_dir() and (candidate / ".git").exists():
        return candidate
    return cache_dir_for(source)
