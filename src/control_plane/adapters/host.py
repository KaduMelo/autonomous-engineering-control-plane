"""The repository host, behind a port.

Only the local stand-in is ever exercised - a pull request is an API concept,
not a git one, so an offline run cannot prove the real host accepts what this
sends. What contains that risk is that both sides implement the contract written
in specs/002-ci-trigger-and-pr/contracts/host_api.md, so a divergence is a
documentation bug rather than a discovery.

There is deliberately no way to merge anything here (FR-013, SC-006). The safest
merge is one that does not exist.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx2

from control_plane import config
from control_plane.domain.errors import HostApiError
from control_plane.domain.models import PullRequestRef

# GitHub returns 422 both for "a pull request already exists for this branch" -
# which is success - and for genuine validation errors. Only the former may be
# treated as a result, so the adapter matches on the message.
_ALREADY_EXISTS = "a pull request already exists"


class RepositoryHostPort(Protocol):
    async def find_open_pull_request(
        self, full_name: str, head_branch: str
    ) -> PullRequestRef | None: ...

    async def create_pull_request(
        self, full_name: str, head: str, base: str, title: str, body: str
    ) -> PullRequestRef: ...


def _classify(response: httpx2.Response) -> None:
    """Raise for a non-2xx, saying whether another attempt could help."""
    if response.status_code < 300:
        return
    retryable = response.status_code == 429 or response.status_code >= 500
    raise HostApiError(
        f"host returned {response.status_code}: {response.text[:400]}",
        retryable=retryable,
    )


def _owner(full_name: str) -> str:
    return full_name.split("/", 1)[0]


class GitHubHost:
    """Speaks the real API. The base URL is what points it at the stand-in."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url or config.HOST_API_BASE_URL
        self._token = token or config.host_token()
        self._client = client or httpx2.AsyncClient(base_url=self._base_url, timeout=30.0)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }

    async def find_open_pull_request(
        self, full_name: str, head_branch: str
    ) -> PullRequestRef | None:
        response = await self._client.get(
            f"/repos/{full_name}/pulls",
            params={"head": f"{_owner(full_name)}:{head_branch}", "state": "open"},
            headers=self._headers,
        )
        _classify(response)
        found: list[dict[str, Any]] = response.json()
        if not found:
            return None
        first = found[0]
        return PullRequestRef(
            number=int(first["number"]), url=str(first["html_url"]), created=False
        )

    async def create_pull_request(
        self, full_name: str, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        response = await self._client.post(
            f"/repos/{full_name}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
            headers=self._headers,
        )

        if response.status_code == 422:
            if _ALREADY_EXISTS in response.text.lower():
                # Another attempt won the race between the query and this call.
                existing = await self.find_open_pull_request(full_name, head)
                if existing is not None:
                    return existing
            raise HostApiError(
                f"host rejected the pull request: {response.text[:400]}", retryable=False
            )

        _classify(response)
        created = response.json()
        return PullRequestRef(
            number=int(created["number"]), url=str(created["html_url"]), created=True
        )
