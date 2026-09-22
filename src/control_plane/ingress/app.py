"""The ingress: one route, no state.

Everything durable lives in the execution engine. This process verifies, decides,
starts and returns - it holds no queue and no retry state, so killing it loses
nothing and the sender redelivers.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from control_plane import config
from control_plane.domain.models import BuildNotification, FixRequest, IngressOutcome
from control_plane.ingress import signature
from control_plane.workflows.fix_workflow import FixWorkflow

logger = logging.getLogger(__name__)

# The decision alone does not determine the status: a rejection can be the
# sender's fault (400, 401) or simply a build that did not fail (200), and the
# sender must be able to tell those apart. Per contracts/ingress.md.
STATUS = {"started": 202, "deduplicated": 200, "rejected": 200, "failed": 503}

# A notification is a handful of fields. Anything larger is not one, and an
# unbounded body is an unbounded read on an endpoint anyone can reach.
MAX_BODY_BYTES = 64 * 1024


def record(outcome: IngressOutcome, status: int | None = None) -> JSONResponse:
    """Every request produces a recorded outcome (FR-015).

    The ingress is the one component whose failures are invisible in the run
    history: a rejected notification produces no run at all, so if it is not
    recorded here it is not recorded anywhere.
    """
    logger.info(
        "ingress %s revision=%s reason=%s",
        outcome.decision,
        outcome.revision,
        outcome.reason,
    )
    return JSONResponse(
        outcome.model_dump(exclude_none=True),
        status_code=status if status is not None else STATUS[outcome.decision],
    )


def create_app(client: Client | None = None) -> Starlette:
    """Build the app. `client` is injected by tests; otherwise connected on startup."""
    state: dict[str, Client | None] = {"client": client}

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        if state["client"] is None:
            from control_plane.worker import connect

            with contextlib.suppress(Exception):
                # A dead engine must not stop the ingress from starting: it
                # answers 503 instead, which is what tells the sender to retry.
                state["client"] = await connect()
        yield

    async def webhook(request: Request) -> JSONResponse:
        body = await request.body()

        if not signature.verify(
            body, request.headers.get("X-Hub-Signature-256"), config.webhook_secret()
        ):
            return record(
                IngressOutcome(decision="rejected", reason="invalid or missing signature"),
                status=401,
            )

        try:
            notification = BuildNotification(**json.loads(body))
        except (ValueError, ValidationError) as exc:
            return record(
                IngressOutcome(decision="rejected", reason=f"malformed payload: {exc}"),
                status=400,
            )

        if not notification.failed:
            return record(
                IngressOutcome(
                    decision="rejected",
                    revision=notification.revision,
                    reason=f"build did not fail (conclusion={notification.conclusion})",
                )
            )

        workflow_id = f"fix-{notification.revision}"
        temporal = state["client"]
        if temporal is None:
            return record(
                IngressOutcome(
                    decision="failed",
                    revision=notification.revision,
                    reason="no connection to the execution engine",
                )
            )

        try:
            await temporal.start_workflow(
                FixWorkflow.run,
                FixRequest(
                    repo_path=notification.repository_url,
                    revision=notification.revision,
                    repository_full_name=notification.repository_full_name,
                ),
                id=workflow_id,
                task_queue=config.TASK_QUEUE,
                # The engine is the deduplication. REJECT_DUPLICATE refuses a
                # second start for this commit in *any* state - running or long
                # finished - so FR-004 and FR-005a are one setting rather than a
                # table, a cursor and a lock we would have to keep consistent.
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            # Not an error. The sender did nothing wrong and must stop retrying,
            # so this is a 200 carrying whatever the earlier run decided.
            previous = None
            try:
                described = await temporal.get_workflow_handle(workflow_id).describe()
                previous = described.status.name if described.status else None
            except Exception:  # noqa: BLE001 - the answer is still "deduplicated"
                logger.warning("could not describe the earlier run %s", workflow_id)
            return record(
                IngressOutcome(
                    decision="deduplicated",
                    revision=notification.revision,
                    workflow_id=workflow_id,
                    previous_status=previous,
                )
            )
        except Exception as exc:
            # Never acknowledge a build no run was started for: the sender's
            # retry queue is the durable one, and a 503 is how it is told.
            return record(
                IngressOutcome(
                    decision="failed",
                    revision=notification.revision,
                    reason=f"could not start a run: {exc}",
                )
            )

        return record(
            IngressOutcome(
                decision="started",
                revision=notification.revision,
                workflow_id=workflow_id,
            )
        )

    return Starlette(
        routes=[Route("/webhook", webhook, methods=["POST"])],
        lifespan=lifespan,
        max_body_size=MAX_BODY_BYTES,
    )


app = create_app()
