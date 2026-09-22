"""Serve the ingress. A separate process from the worker, on purpose."""

from __future__ import annotations

import logging

import uvicorn

from control_plane import config
from control_plane.ingress.app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not config.webhook_secret():
        raise SystemExit(
            f"{config.WEBHOOK_SECRET_ENV} is not set - the ingress would reject everything"
        )
    uvicorn.run(
        create_app(), host=config.INGRESS_HOST, port=config.INGRESS_PORT, log_level="warning"
    )


if __name__ == "__main__":
    main()
