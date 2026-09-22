"""The clone cache must be a pure function of its inputs (T009, Principle I).

What makes this safe is not that it caches, but that a miss is recoverable by
re-deriving from the same inputs. That is the difference between a cache and
state crossing an activity boundary - the thing feature 001 refuses to do.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from control_plane.activities.clone import ensure_clone
from control_plane.adapters import repo
from control_plane.domain.models import CloneInput


@pytest.fixture
def local_seed(tmp_path_factory) -> tuple[str, str]:
    """A working repository on disk - the shape feature 001's CLI passes."""
    root = Path(__file__).resolve().parents[2]
    dest = tmp_path_factory.mktemp("clone-seed") / "repo"
    subprocess.run(
        [str(root / "scripts" / "seed_repo.sh"), str(dest)], check=True, capture_output=True
    )
    revision = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return str(dest), revision


@pytest.fixture
def seed(local_seed, tmp_path_factory) -> tuple[str, str]:
    """A *bare* repository, which is the shape a real remote has.

    This matters: resolve_source treats a working repository as the repository
    itself and does not copy it, so only a bare source exercises the clone path.
    """
    source, revision = local_seed
    bare = tmp_path_factory.mktemp("clone-remote") / "origin.git"
    subprocess.run(["git", "clone", "--bare", source, str(bare)], check=True, capture_output=True)
    return str(bare), revision


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_CLONE_ROOT", str(tmp_path / "clones"))


def test_the_directory_is_a_deterministic_function_of_the_source():
    a = repo.cache_dir_for("https://example.invalid/owner/name.git")
    b = repo.cache_dir_for("https://example.invalid/owner/name.git")
    c = repo.cache_dir_for("https://example.invalid/owner/other.git")
    assert a == b
    assert a != c


async def test_a_miss_clones_and_the_revision_resolves(seed):
    source, revision = seed
    await ActivityEnvironment().run(ensure_clone, CloneInput(source=source, revision=revision))
    assert repo.resolve(repo.cache_dir_for(source), revision) == revision


async def test_calling_twice_converges(seed):
    source, revision = seed
    payload = CloneInput(source=source, revision=revision)
    env = ActivityEnvironment()

    first = await env.run(ensure_clone, payload)
    second = await env.run(ensure_clone, payload)

    assert first == second
    assert repo.resolve(repo.cache_dir_for(source), revision) == revision


async def test_deleting_the_cache_converges_again(seed):
    """A miss is recoverable. That is what makes this a cache and not state."""
    import shutil

    source, revision = seed
    payload = CloneInput(source=source, revision=revision)
    env = ActivityEnvironment()

    await env.run(ensure_clone, payload)
    shutil.rmtree(repo.cache_dir_for(source))
    await env.run(ensure_clone, payload)

    assert repo.resolve(repo.cache_dir_for(source), revision) == revision


async def test_a_local_repository_is_used_in_place_and_never_copied(local_seed):
    """The deliberate deviation from the spec's "one mechanism" wording.

    Cloning a local path would work, but the fix branch would land in a cache
    directory the operator never looks at - which is exactly what feature 001's
    demo shows them.
    """
    source, revision = local_seed
    await ActivityEnvironment().run(ensure_clone, CloneInput(source=source, revision=revision))
    assert repo.resolve_source(source) == Path(source)
    assert not repo.cache_dir_for(source).exists(), "a local repository must not be copied"


async def test_a_new_commit_on_the_source_is_fetched_on_the_next_call(seed, local_seed):
    """A hit must fetch, or the cache would serve a stale repository forever."""
    source, revision = seed
    working, _ = local_seed
    env = ActivityEnvironment()
    await env.run(ensure_clone, CloneInput(source=source, revision=revision))

    marker = Path(working) / "NEW.md"
    marker.write_text("added after the first clone\n")
    subprocess.run(["git", "-C", working, "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", working, "commit", "-m", "second"],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t.invalid",
            "PATH": "/usr/bin:/bin",
        },
    )
    subprocess.run(
        ["git", "-C", working, "push", "--quiet", source, "HEAD:main"],
        check=True,
        capture_output=True,
    )
    newer = subprocess.run(
        ["git", "-C", working, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()

    await env.run(ensure_clone, CloneInput(source=source, revision=newer))
    assert repo.resolve(repo.cache_dir_for(source), newer) == newer


async def test_a_missing_revision_is_not_retryable(seed):
    """A force-push will not undo itself, so retrying cannot help."""
    from temporalio.exceptions import ApplicationError

    source, _ = seed
    with pytest.raises(ApplicationError) as caught:
        await ActivityEnvironment().run(ensure_clone, CloneInput(source=source, revision="f" * 40))
    assert caught.value.non_retryable is True


async def test_an_unreachable_source_raises_a_retryable_clone_error(tmp_path):
    from control_plane.domain.errors import CloneError

    with pytest.raises(CloneError):
        await ActivityEnvironment().run(
            ensure_clone,
            CloneInput(source=str(tmp_path / "does-not-exist"), revision="a" * 40),
        )
