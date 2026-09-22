#!/usr/bin/env python
"""Sign a build-failure notification and post it to the ingress.

Signing in bash with openssl is doable and brittle; this uses the same signing
code the ingress verifies with, so a mismatch is impossible by construction.

    python scripts/send_webhook.py --repo-url <path-or-url> --full-name owner/name \
        --revision <sha> [--times N]

`--times N` sends N notifications concurrently, which is what a CI platform
retrying a delivery and several failing checks look like together.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx2  # noqa: E402

from control_plane import config  # noqa: E402
from control_plane.ingress import signature  # noqa: E402


async def _send(client: httpx2.AsyncClient, body: bytes) -> tuple[int, dict]:
    response = await client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature.sign(body, config.webhook_secret()),
        },
    )
    return response.status_code, response.json()


async def main(args: argparse.Namespace) -> int:
    body = json.dumps(
        {
            "repository_url": args.repo_url,
            "repository_full_name": args.full_name,
            "revision": args.revision,
            "conclusion": args.conclusion,
        }
    ).encode()

    base = f"http://{config.INGRESS_HOST}:{config.INGRESS_PORT}"
    async with httpx2.AsyncClient(base_url=base, timeout=30.0) as client:
        results = await asyncio.gather(*[_send(client, body) for _ in range(args.times)])

    for status, payload in results:
        print(f"    HTTP {status}  {payload.get('decision')}  {payload.get('reason', '')}".rstrip())

    started = sum(1 for status, _ in results if status == 202)
    deduplicated = sum(1 for _, p in results if p.get("decision") == "deduplicated")
    if args.times > 1:
        print(f"\n    {started} started, {deduplicated} deduplicated")
        if started != 1:
            print(f"    FAIL: expected exactly 1 run to start, {started} did")
            return 1
    return 0 if started or deduplicated else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="send_webhook")
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--conclusion", default="failure")
    parser.add_argument("--times", type=int, default=1)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
