"""The isolation boundary, and the exit-code contract that reads its result.

The isolation flags are passed as literal arguments rather than through a
Docker client library on purpose: a reviewer auditing this system's security
boundary should be able to read it as one list, not reconstruct it from keyword
arguments spread across a call.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from control_plane import config
from control_plane.domain.errors import TestRunnerError
from control_plane.domain.models import MAX_OUTPUT_CHARS, TestResult, truncate

# The only two exit codes that are results. Everything else is an error.
EXIT_ALL_PASSED = 0
EXIT_TESTS_FAILED = 1

# Exit code docker reports when the container is killed (SIGKILL = 128 + 9).
EXIT_SIGKILL = 137

_FAILED_LINE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)


@dataclass(frozen=True)
class SandboxRun:
    """Raw outcome of one container run. Not yet a verdict."""

    exit_code: int
    output: str
    duration_s: float


def docker_run_argv(
    workspace: Path,
    command: list[str],
    image: str | None = None,
    name: str | None = None,
) -> list[str]:
    """Build the container invocation.

    Every flag below closes something specific:
      --network=none   exfiltration, and run-time dependency fetching
      --read-only      writes anywhere outside the declared mounts
      --tmpfs          scratch the suite needs, mounted non-executable
      bind mount       the throwaway workspace, the only writable project path
      --user           root inside the container
      --memory/--pids/--cpus   resource exhaustion reaching the host
      --rm             leftover containers
    No --env is passed, so no credential can reach generated code.

    `image` and the timeouts resolve from config at call time, not as default
    arguments: a default is evaluated once at import, which would make the
    setting impossible to override afterwards.
    """
    image = image or config.SANDBOX_IMAGE
    argv = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--mount",
        f"type=bind,src={workspace.resolve()},dst=/work",
        "--user",
        str(config.SANDBOX_UID),
        "--memory",
        config.SANDBOX_MEMORY,
        "--pids-limit",
        str(config.SANDBOX_PIDS_LIMIT),
        "--cpus",
        config.SANDBOX_CPUS,
        "--workdir",
        "/work",
    ]
    if name:
        argv += ["--name", name]
    argv += [image, *command]
    return argv


async def run_command(
    workspace: Path, command: list[str], timeout_s: float | None = None
) -> SandboxRun:
    """Run `command` in the sandbox and capture its combined output.

    A run that exceeds `timeout_s` is force-removed and reported as exit 137 -
    the same code the kernel's own kill produces - so the exit-code table stays
    the single place that decides what an outcome means.
    """
    timeout_s = config.SANDBOX_WALL_CLOCK_S if timeout_s is None else timeout_s
    grant_workspace_access(workspace)
    name = f"fixloop-{uuid.uuid4().hex[:12]}"
    argv = docker_run_argv(workspace, command, name=name)
    started = time.monotonic()

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        exit_code = proc.returncode if proc.returncode is not None else EXIT_SIGKILL
        output = stdout.decode("utf-8", "replace")
    except TimeoutError:
        await _force_remove(name)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        exit_code = EXIT_SIGKILL
        output = f"container exceeded the {timeout_s:.0f}s wall-clock limit and was killed"

    return SandboxRun(
        exit_code=exit_code,
        output=truncate(output, MAX_OUTPUT_CHARS),
        duration_s=time.monotonic() - started,
    )


def grant_workspace_access(workspace: Path) -> None:
    """Make the throwaway workspace writable by the container's user.

    The container deliberately runs as a fixed uid that means nothing on the
    host, so bind-mounted files owned by the host user are not writable by it -
    and the suite under test legitimately writes (pycache, temp fixtures). The
    alternative, running as the host's own uid, would hand generated code a uid
    that owns real files outside the container. Widening permissions on a
    directory that is deleted after the run is the cheaper trade.
    """
    workspace.chmod(0o777)
    for path in workspace.rglob("*"):
        path.chmod(0o777 if path.is_dir() else 0o666)


async def destroy_workspace(workspace: Path) -> None:
    """Delete a throwaway workspace, including anything the container wrote.

    Files created inside the container belong to its uid, in directories the
    host user has no write permission on, so a plain rmtree leaves them behind -
    silently, if errors are ignored. The fallback removes them from inside a
    root container, which is the only party that can.
    """
    shutil.rmtree(workspace, ignore_errors=True)
    if not workspace.exists():
        return

    proc = await asyncio.create_subprocess_exec(
        *docker_run_argv(
            workspace,
            ["sh", "-c", "rm -rf /work/..?* /work/.[!.]* /work/*"],
        )[:1]
        + [
            "run",
            "--rm",
            "--network=none",
            "--user",
            "0",
            "--mount",
            f"type=bind,src={workspace.resolve()},dst=/work",
            config.SANDBOX_IMAGE,
            "sh",
            "-c",
            "rm -rf /work/..?* /work/.[!.]* /work/* 2>/dev/null || true",
        ],
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    shutil.rmtree(workspace, ignore_errors=True)


async def _force_remove(name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "--force",
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def run_suite(workspace: Path, timeout_s: float | None = None) -> SandboxRun:
    """Run the repository's pytest suite inside the sandbox.

    `-p no:cacheprovider` matters: with --read-only, pytest's cache write fails
    and floods the output with warnings that have nothing to do with the code
    under test.
    """
    return await run_command(
        workspace,
        ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        timeout_s=timeout_s,
    )


def verdict(run: SandboxRun) -> TestResult:
    """Turn a container run into a verdict, or raise.

    | exit | meaning                        | contract      |
    |------|--------------------------------|---------------|
    | 0    | all tests passed               | result        |
    | 1    | tests failed                   | result        |
    | 2-5  | pytest interrupted/internal/   | TestRunnerError
    |      | usage error / no tests found   |               |
    | 125-127 | docker or command failure   | TestRunnerError
    | 137  | killed (OOM, or our timeout)   | TestRunnerError
    | else | unknown                        | TestRunnerError
    """
    if run.exit_code == EXIT_ALL_PASSED:
        return TestResult(passed=True, output=run.output, duration_s=run.duration_s)
    if run.exit_code == EXIT_TESTS_FAILED:
        return TestResult(
            passed=False,
            output=run.output,
            failing_tests=_FAILED_LINE.findall(run.output),
            duration_s=run.duration_s,
        )
    raise TestRunnerError(
        f"the validation environment failed (exit {run.exit_code}): {truncate(run.output, 2000)}"
    )
