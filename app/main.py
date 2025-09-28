import time
from typing import Awaitable

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from app import config

config.setup_telemetry()
logger = structlog.get_logger()

app = FastAPI()
config.instrument_fastapi(app)

active_users = 0


@app.middleware("http")
async def handle_incoming_requests(request: Request, call_next: Awaitable):
    route_attrs = {"method": request.method, "path": request.url.path}

    global active_users
    active_users += 1
    config.set_gauge(active_users, route_attrs)

    logger.debug("Request received", **route_attrs)
    start_time = time.perf_counter()

    response = await call_next(request)

    process_time = time.perf_counter() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.2f}"

    config.increment_counter(1, route_attrs)
    config.record_histogram(process_time, route_attrs)

    # we are considering each user to be active only for the duration of the request
    active_users -= 1
    config.set_gauge(active_users, route_attrs)

    logger.debug(
        "Request processed",
        process_time=f"{process_time:2f}",
        **route_attrs,
        status_code=response.status_code,
    )
    return response


@app.get("/fast")
def fast_response():
    return {"message": "fast_response"}


@app.get("/slow")
def slow_response():
    time.sleep(2)
    return {"message": "slow_response"}


@app.get("/error")
def error_response():
    logger.error("Mocking an application error")
    raise HTTPException(status_code=500, detail="error_response")


@app.get("/metrics")
def metrics():
    """Exposes application metrics in a Prometheus compatible format."""

    registry = config.prometheus_registry
    if not registry:
        return HTTPException(status_code=400, detail="Prometheus observability platform not configured")

    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
