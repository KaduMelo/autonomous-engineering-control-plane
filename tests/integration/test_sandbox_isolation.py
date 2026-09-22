"""Isolation is a security boundary, so it is asserted twice: on the flags we
pass, and on what actually happens inside a real container (SC-004).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from control_plane.adapters.sandbox import docker_run_argv, run_command

pytestmark = pytest.mark.docker


def _docker_available() -> bool:
    return shutil.which("docker") is not None


requires_docker = pytest.mark.skipif(not _docker_available(), reason="docker not available")


def test_argv_declares_every_isolation_flag(tmp_path: Path):
    argv = docker_run_argv(tmp_path, ["python", "-c", "pass"])
    joined = " ".join(argv)

    assert "--network=none" in joined, "no network egress from generated code"
    assert "--read-only" in joined, "no writes outside the declared mounts"
    assert "--rm" in joined, "no leftover containers"
    assert "--user" in joined, "never root inside the container"
    assert "--memory" in joined, "memory exhaustion must not reach the host"
    assert "--pids-limit" in joined, "fork bombs must not reach the host"
    assert "--tmpfs" in joined, "scratch space is non-executable tmpfs"


def test_argv_carries_no_environment_secrets(tmp_path: Path):
    argv = docker_run_argv(tmp_path, ["python", "-c", "pass"])
    assert "--env" not in argv and "-e" not in argv


def test_argv_mounts_only_the_workspace(tmp_path: Path):
    argv = docker_run_argv(tmp_path, ["python", "-c", "pass"])
    mounts = [a for a in argv if a.startswith("type=bind")]
    assert len(mounts) == 1
    assert str(tmp_path.resolve()) in mounts[0]


@requires_docker
async def test_generated_code_cannot_reach_the_network(tmp_path: Path):
    result = await run_command(
        tmp_path,
        ["python", "-c", "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"],
        timeout_s=30.0,
    )
    assert result.exit_code != 0
    assert "unreachable" in result.output.lower() or "network" in result.output.lower()


@requires_docker
async def test_generated_code_cannot_write_outside_the_workspace(tmp_path: Path):
    result = await run_command(
        tmp_path,
        ["python", "-c", "open('/etc/pwned', 'w').write('x')"],
        timeout_s=30.0,
    )
    assert result.exit_code != 0
    assert "read-only" in result.output.lower() or "permission" in result.output.lower()


@requires_docker
async def test_the_workspace_itself_is_writable(tmp_path: Path):
    """The suite under test legitimately writes here - pycache, temp fixtures."""
    result = await run_command(
        tmp_path,
        ["python", "-c", "open('/work/marker', 'w').write('ok')"],
        timeout_s=30.0,
    )
    assert result.exit_code == 0
    assert (tmp_path / "marker").read_text() == "ok"


@requires_docker
async def test_the_host_filesystem_is_not_visible(tmp_path: Path):
    result = await run_command(
        tmp_path,
        ["python", "-c", "import os; print(os.listdir('/work'))"],
        timeout_s=30.0,
    )
    assert result.exit_code == 0
    assert "src" not in result.output, "the control plane's own tree must not be mounted"
