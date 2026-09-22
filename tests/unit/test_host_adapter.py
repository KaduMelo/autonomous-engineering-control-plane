"""How the repository host's answers are classified (T021, Principle V).

The adapter is where a network becomes a contract. The case worth reading twice
is 422: the host returns it both for "a pull request already exists" - which is
success - and for genuine validation errors, which are not.
"""

from __future__ import annotations

import ast
import inspect

import httpx2
import pytest

from control_plane.adapters import host
from control_plane.domain.errors import HostApiError

FULL_NAME = "owner/name"
BRANCH = "fix/" + "a" * 40


def _host(handler) -> host.GitHubHost:
    transport = httpx2.MockTransport(handler)
    return host.GitHubHost(
        base_url="https://host.invalid",
        token="t",
        client=httpx2.AsyncClient(transport=transport, base_url="https://host.invalid"),
    )


async def test_creating_returns_a_created_reference():
    def handler(request):
        if request.method == "GET":
            return httpx2.Response(200, json=[])
        return httpx2.Response(201, json={"number": 7, "html_url": "https://host/pr/7"})

    ref = await _host(handler).create_pull_request(FULL_NAME, BRANCH, "main", "t", "b")
    assert ref.number == 7
    assert ref.created is True


async def test_an_existing_open_pull_request_is_found_and_not_created():
    def handler(request):
        assert request.method == "GET", "create must not be called when one exists"
        return httpx2.Response(200, json=[{"number": 3, "html_url": "https://host/pr/3"}])

    ref = await _host(handler).find_open_pull_request(FULL_NAME, BRANCH)
    assert ref is not None
    assert ref.created is False


async def test_no_open_pull_request_returns_none():
    ref = await _host(lambda r: httpx2.Response(200, json=[])).find_open_pull_request(
        FULL_NAME, BRANCH
    )
    assert ref is None


async def test_422_already_exists_is_a_result_not_an_error():
    """Another attempt won the race between the query and the create."""

    def handler(request):
        # create_pull_request does not query first - that is the activity's job.
        # The only GET here is the re-query after the 422.
        if request.method == "GET":
            return httpx2.Response(200, json=[{"number": 9, "html_url": "https://host/pr/9"}])
        return httpx2.Response(
            422,
            json={"errors": [{"message": "A pull request already exists for owner:fix/x."}]},
        )

    ref = await _host(handler).create_pull_request(FULL_NAME, BRANCH, "main", "t", "b")
    assert ref.number == 9
    assert ref.created is False


async def test_other_422s_are_not_retryable():
    def handler(request):
        if request.method == "GET":
            return httpx2.Response(200, json=[])
        return httpx2.Response(422, json={"errors": [{"message": "base is invalid"}]})

    with pytest.raises(HostApiError) as caught:
        await _host(handler).create_pull_request(FULL_NAME, BRANCH, "main", "t", "b")
    assert caught.value.retryable is False


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_credential_and_not_found_failures_are_not_retryable(status):
    """A bad token or a missing repository will not fix itself in three tries."""
    with pytest.raises(HostApiError) as caught:
        await _host(lambda r: httpx2.Response(status, json={})).find_open_pull_request(
            FULL_NAME, BRANCH
        )
    assert caught.value.retryable is False


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_host_trouble_is_retryable(status):
    with pytest.raises(HostApiError) as caught:
        await _host(lambda r: httpx2.Response(status, json={})).find_open_pull_request(
            FULL_NAME, BRANCH
        )
    assert caught.value.retryable is True


async def test_the_token_is_sent_as_a_bearer_credential():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx2.Response(200, json=[])

    await _host(handler).find_open_pull_request(FULL_NAME, BRANCH)
    assert seen["auth"] == "Bearer t"


def test_the_adapter_has_no_way_to_merge_anything():
    """SC-006. Checked on the code, not the text - the docstring says the word."""
    tree = ast.parse(inspect.getsource(host))

    named = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and "merge" in node.name.lower()
    ]
    assert not named, f"no function may be a merge path: {named}"

    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "/merge" in node.value.lower()
    ]
    assert not literals, f"no request path may reach a merge endpoint: {literals}"
