"""The ingress against a real execution engine (T012, SC-003, SC-007)."""

from __future__ import annotations

import json
import time

import httpx2
import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from control_plane.ingress import signature
from control_plane.ingress.app import create_app

SECRET = b"test-secret"
REVISION = "a" * 40

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


def _client(app) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ingress.invalid"
    )


async def _post(app, payload: dict | None = None, *, body: bytes | None = None, sign=True):
    raw = body if body is not None else json.dumps(payload or VALID).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = signature.sign(raw, SECRET)
    async with _client(app) as client:
        return await client.post("/webhook", content=raw, headers=headers)


async def test_a_signed_failure_starts_a_run(engine):
    response = await _post(create_app(engine.client))

    assert response.status_code == 202
    body = response.json()
    assert body["decision"] == "started"
    assert body["workflow_id"] == f"fix-{REVISION}"

    handle = engine.client.get_workflow_handle(f"fix-{REVISION}")
    assert (await handle.describe()) is not None


async def test_an_unsigned_notification_starts_nothing(engine):
    response = await _post(create_app(engine.client), sign=False)

    assert response.status_code == 401, "per contracts/ingress.md, not a generic rejection"
    assert response.json()["decision"] == "rejected"


async def test_a_tampered_body_starts_nothing(engine):
    app = create_app(engine.client)
    raw = json.dumps(VALID).encode()
    headers = {"X-Hub-Signature-256": signature.sign(raw, SECRET)}
    async with _client(app) as client:
        response = await client.post("/webhook", content=raw + b" ", headers=headers)

    assert response.status_code == 401
    assert response.json()["decision"] == "rejected"


async def test_a_malformed_body_is_rejected(engine):
    response = await _post(create_app(engine.client), body=b"not json at all")
    assert response.status_code == 400
    assert response.json()["decision"] == "rejected"
    assert "malformed" in response.json()["reason"]


async def test_a_missing_field_is_rejected(engine):
    payload = {k: v for k, v in VALID.items() if k != "revision"}
    response = await _post(create_app(engine.client), payload)
    assert response.status_code == 400
    assert response.json()["decision"] == "rejected"


async def test_a_successful_build_starts_nothing(engine):
    response = await _post(create_app(engine.client), {**VALID, "conclusion": "success"})

    assert response.status_code == 200
    assert response.json()["decision"] == "rejected"
    assert "did not fail" in response.json()["reason"]


async def test_an_unreachable_engine_is_a_503_and_never_an_acknowledgement():
    """Acknowledging a build no run was started for is the failure that loses work."""
    response = await _post(create_app(None))

    assert response.status_code == 503
    assert response.json()["decision"] == "failed"


async def test_the_response_does_not_wait_for_the_run(engine):
    """SC-007. No worker is running here, so the run cannot progress at all."""
    started = time.monotonic()
    response = await _post(create_app(engine.client))
    elapsed = time.monotonic() - started

    assert response.status_code == 202
    assert elapsed < 5.0, f"the ingress blocked for {elapsed:.1f}s"


async def test_every_response_carries_a_decision(engine):
    app = create_app(engine.client)
    for payload, kwargs in [
        (VALID, {}),
        (VALID, {"sign": False}),
        ({**VALID, "conclusion": "success"}, {}),
    ]:
        response = await _post(app, payload, **kwargs)
        assert "decision" in response.json()
