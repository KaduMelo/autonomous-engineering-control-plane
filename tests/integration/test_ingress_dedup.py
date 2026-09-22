"""One commit, one run (T016, T017, SC-002, SC-003a).

There is no deduplication code in this repository. The guarantee comes from
`workflow_id = fix-<sha>` plus REJECT_DUPLICATE, which makes the engine refuse a
second start in any state - so these tests are really checking that the ingress
turns that refusal into the right answer rather than an error.
"""

from __future__ import annotations

import asyncio
import json

import httpx2
import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from control_plane.ingress import signature
from control_plane.ingress.app import create_app

SECRET = b"test-secret"
REVISION = "b" * 40

VALID = {
    "repository_url": "https://host.invalid/owner/name.git",
    "repository_full_name": "owner/name",
    "revision": REVISION,
    "conclusion": "failure",
}


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_WEBHOOK_SECRET", SECRET.decode())


@pytest.fixture
async def engine():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        yield env


async def _post(app) -> httpx2.Response:
    raw = json.dumps(VALID).encode()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ingress.invalid"
    ) as client:
        return await client.post(
            "/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": signature.sign(raw, SECRET)},
        )


async def test_ten_concurrent_deliveries_produce_one_run(engine):
    """SC-002. CI platforms retry, re-run jobs, and report several failing checks."""
    app = create_app(engine.client)

    responses = await asyncio.gather(*[_post(app) for _ in range(10)])

    started = [r for r in responses if r.status_code == 202]
    deduplicated = [r for r in responses if r.json()["decision"] == "deduplicated"]
    assert len(started) == 1, "exactly one delivery may start a run"
    assert len(deduplicated) == 9


async def test_a_duplicate_is_success_so_the_sender_stops_retrying(engine):
    app = create_app(engine.client)

    first = await _post(app)
    second = await _post(app)

    assert first.status_code == 202
    assert second.status_code == 200, "a duplicate is not the sender's fault"
    assert second.json()["decision"] == "deduplicated"
    assert second.json()["workflow_id"] == f"fix-{REVISION}"


async def test_a_duplicate_reports_what_the_earlier_run_is_doing(engine):
    app = create_app(engine.client)
    await _post(app)

    second = await _post(app)

    assert second.json().get("previous_status") is not None


async def test_two_different_commits_produce_two_runs(engine):
    app = create_app(engine.client)
    raw_other = json.dumps({**VALID, "revision": "c" * 40}).encode()

    first = await _post(app)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ingress.invalid"
    ) as client:
        second = await client.post(
            "/webhook",
            content=raw_other,
            headers={"X-Hub-Signature-256": signature.sign(raw_other, SECRET)},
        )

    assert first.status_code == 202
    assert second.status_code == 202


async def test_a_notification_for_a_finished_commit_starts_nothing(engine):
    """SC-003a. One broken commit has one answer, and it is not re-bought."""
    app = create_app(engine.client)
    await _post(app)

    # Close the run, then notify again as a CI platform re-running an old job would.
    await engine.client.get_workflow_handle(f"fix-{REVISION}").terminate("test")
    response = await _post(app)

    assert response.status_code == 200
    assert response.json()["decision"] == "deduplicated"
    assert response.json()["previous_status"] == "TERMINATED"
