#!/usr/bin/env python
"""A runnable stand-in for the repository host.

Implements the two endpoints in
specs/002-ci-trigger-and-pr/contracts/host_api.md over in-memory state. It is a
process, not a mock, so the demo exercises a real HTTP round trip and the
adapter is unchanged between offline and a real host - only its base URL moves.

    python scripts/fake_host.py

It answers 422 with GitHub's wording when a pull request already exists for a
branch, because that path is a *result* in the adapter and a fake that never
produced it would leave the interesting branch untested.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

# full_name -> head branch -> pull request
_STATE: dict[str, dict[str, dict]] = {}
_NEXT_NUMBER = [1]


def _head_branch(raw: str) -> str:
    """`owner:branch` from the query, or a bare branch name."""
    return raw.split(":", 1)[1] if ":" in raw else raw


async def list_pulls(request: Request) -> JSONResponse:
    full_name = f"{request.path_params['owner']}/{request.path_params['repo']}"
    head = _head_branch(request.query_params.get("head", ""))
    found = _STATE.get(full_name, {}).get(head)
    return JSONResponse([found] if found else [])


async def create_pull(request: Request) -> JSONResponse:
    full_name = f"{request.path_params['owner']}/{request.path_params['repo']}"
    payload = await request.json()
    head = _head_branch(payload.get("head", ""))

    if head in _STATE.get(full_name, {}):
        return JSONResponse(
            {"errors": [{"message": f"A pull request already exists for {head}."}]},
            status_code=422,
        )

    number = _NEXT_NUMBER[0]
    _NEXT_NUMBER[0] += 1
    pull = {
        "number": number,
        "html_url": f"http://127.0.0.1:8099/{full_name}/pull/{number}",
        "head": {"ref": head},
        "title": payload.get("title", ""),
        "body": payload.get("body", ""),
    }
    _STATE.setdefault(full_name, {})[head] = pull
    return JSONResponse(pull, status_code=201)


async def reset(request: Request) -> JSONResponse:
    """Test and demo affordance. Not part of the host contract."""
    _STATE.clear()
    _NEXT_NUMBER[0] = 1
    return JSONResponse({"ok": True})


app = Starlette(
    routes=[
        Route("/repos/{owner}/{repo}/pulls", list_pulls, methods=["GET"]),
        Route("/repos/{owner}/{repo}/pulls", create_pull, methods=["POST"]),
        Route("/_reset", reset, methods=["POST"]),
    ]
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
