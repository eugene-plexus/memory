"""GET /healthz — liveness / readiness probe."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import __version__
from .._generated.models import Health, Status

router = APIRouter(tags=["meta"])


@router.get("/healthz", response_model=Health)
async def healthz(request: Request) -> Health:
    # The component always serves /healthz so config endpoints stay
    # reachable. When store init fails at startup, surface that as
    # `degraded` so consumers can tell the component is alive but can't
    # serve conversation data until the operator fixes config.
    store = getattr(request.app.state, "store", None)
    store_error = getattr(request.app.state, "store_error", None)

    if store is None:
        return Health(
            status=Status.degraded,
            version=__version__,
            component="memory",
            details={"store_error": store_error},
        )

    return Health(
        status=Status.ok,
        version=__version__,
        component="memory",
    )
