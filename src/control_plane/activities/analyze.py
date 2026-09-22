"""Gather what the fixer needs to see.

The failing tests are not an input: they come from the baseline validation run,
so discovery and validation can never disagree about what is broken.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from control_plane.adapters import repo
from control_plane.domain.models import (
    MAX_SOURCE_CHARS,
    AnalyzeInput,
    RepositoryContext,
    SourceFile,
    truncate,
)

_NODE_FILE = re.compile(r"^([^:]+\.py)")
_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", re.MULTILINE)


def _local_modules(source: str, roots: list[Path], workspace: Path) -> set[Path]:
    """Files imported by `source` that live in this repository."""
    found: set[Path] = set()
    for dotted in _IMPORT.findall(source):
        relative = Path(*dotted.split(".")).with_suffix(".py")
        for root in roots:
            candidate = root / relative
            if candidate.is_file():
                found.add(candidate)
            package = root / Path(*dotted.split(".")) / "__init__.py"
            if package.is_file():
                found.add(package)
    return {p for p in found if workspace in p.parents or p.parent == workspace}


@activity.defn
async def analyze_repo(request: AnalyzeInput) -> RepositoryContext:
    """Collect the failing test files and the modules they import."""
    source = repo.resolve_source(request.repo_path)
    if repo.resolve(source, request.revision) is None:
        # Retrying cannot conjure a commit that is not there.
        raise ApplicationError(
            f"revision {request.revision} does not exist in {request.repo_path}",
            type="UnknownRevision",
            non_retryable=True,
        )

    workspace = Path(tempfile.mkdtemp(prefix="analyze-"))
    try:
        repo.materialize(source, request.revision, workspace)
        roots = [workspace, workspace / "src", workspace / "tests"]

        wanted: list[Path] = []
        for node in request.baseline.failing_tests:
            match = _NODE_FILE.match(node)
            if not match:
                continue
            path = workspace / match.group(1)
            if path.is_file() and path not in wanted:
                wanted.append(path)

        for test_file in list(wanted):
            for module in sorted(_local_modules(test_file.read_text(), roots, workspace)):
                if module not in wanted:
                    wanted.append(module)

        sources = [
            SourceFile(
                path=str(path.relative_to(workspace)),
                content=truncate(path.read_text(), MAX_SOURCE_CHARS),
            )
            for path in wanted
        ]
        return RepositoryContext(
            failing_tests=list(request.baseline.failing_tests),
            failure_output=request.baseline.output,
            sources=sources,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
