"""Nothing this feature does reaches the internet by default (SC-010).

The claim is narrow on purpose. It is not "this suite runs on an unplugged
machine" - the Temporal test server is a binary the toolchain fetches once, and
pretending otherwise would be a false absolute. It is: no repository host, no CI
platform, and no token are contacted, and every default endpoint is loopback.
"""

from __future__ import annotations

import ast
import inspect
from urllib.parse import urlparse

import pytest

from control_plane import config
from control_plane.adapters import host
from control_plane.ingress import app as ingress_app

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@pytest.mark.parametrize(
    "endpoint", [config.HOST_API_BASE_URL, f"http://{config.INGRESS_HOST}", config.TEMPORAL_TARGET]
)
def test_every_default_endpoint_is_loopback(endpoint):
    parsed = urlparse(endpoint if "//" in endpoint else f"//{endpoint}")
    assert parsed.hostname in LOOPBACK, f"{endpoint} points off this machine by default"


def test_no_module_hardcodes_a_public_host():
    for module in (host, ingress_app, config):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            value = node.value
            if not value.startswith(("http://", "https://")):
                continue
            hostname = urlparse(value).hostname or ""
            assert hostname in LOOPBACK or hostname.endswith((".invalid", ".example")), (
                f"{module.__name__} hardcodes {value}"
            )


def test_no_token_is_required_to_run():
    """A suite that needs a credential to pass is a suite nobody runs."""
    assert config.host_token(), "there must be a usable default for the offline stand-in"


def test_an_empty_environment_variable_behaves_like_an_unset_one(monkeypatch):
    """The bug CI found and the local suite could not.

    `os.environ.get(KEY, default)` returns "" for a variable that is set but
    empty, so the default never applies. Anything here with a usable default
    must treat the two the same.
    """
    import importlib

    monkeypatch.setenv("CONTROL_PLANE_HOST_TOKEN", "")
    monkeypatch.setenv("CONTROL_PLANE_HOST_API", "")
    monkeypatch.setenv("CONTROL_PLANE_TASK_QUEUE", "")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.host_token(), "an empty token must fall back to the default"
        assert reloaded.HOST_API_BASE_URL.startswith("http")
        assert reloaded.TASK_QUEUE
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_the_webhook_secret_deliberately_does_not_fall_back(monkeypatch):
    """The one exception, and it is a security property, not an oversight.

    An unset secret must reject every notification rather than quietly adopt a
    default that an attacker could also guess.
    """
    monkeypatch.setenv("CONTROL_PLANE_WEBHOOK_SECRET", "")
    assert config.webhook_secret() == b""
