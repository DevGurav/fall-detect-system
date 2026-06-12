"""Structured logging + per-request trace IDs (Phase 32 observability).

`configure_logging()` wires structlog and the stdlib `logging` root into a single
sink that emits **one JSON object per line on stdout** — the shape Fly.io's log
shipper forwards to Better Stack. Both our own `structlog.get_logger()` calls and
foreign loggers (uvicorn, sqlalchemy) flow through the same `ProcessorFormatter`,
so there is exactly one log format in production.

`TraceIDMiddleware` mints a `trace_id` per request (honouring an inbound
`X-Request-ID` / `X-Trace-ID` if the caller sets one), binds it into structlog's
contextvars so every log line emitted while handling that request carries it,
echoes it back on the response, and logs a `request_completed` line with method,
path, status and duration.

It is a raw ASGI middleware on purpose: Starlette's `BaseHTTPMiddleware` buffers
the response body, which would break the long-lived SSE caregiver feed
(`GET /v1/events/stream`, Phase 27). This wrapper only touches the response-start
message, so streaming responses pass through untouched.

Better Stack: set `FG_BETTER_STACK_TOKEN` to also ship logs via `logtail-python`
(optional extra `observability`). If the token is set but the package is missing,
we warn once and stay on stdout rather than failing boot.
"""
from __future__ import annotations

import logging
import sys
import time
import uuid
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, MutableMapping

    from app.config import Settings

    Scope = MutableMapping[str, object]
    Message = MutableMapping[str, object]
    Receive = Callable[[], Awaitable[Message]]
    Send = Callable[[Message], Awaitable[None]]

_log = structlog.get_logger("fall_guardian")


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging to emit JSON on stdout (idempotent)."""
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,   # pulls in the per-request trace_id
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,        # format logs from non-structlog loggers too
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)

    # Let uvicorn's loggers propagate to the root sink instead of double-printing
    # with their own default handlers.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True

    _maybe_attach_better_stack(settings)


def _maybe_attach_better_stack(settings: Settings) -> None:
    """Attach a Better Stack (Logtail) handler when FG_BETTER_STACK_TOKEN is set."""
    token = settings.better_stack_token
    if not token:
        return
    try:
        from logtail import LogtailHandler
    except ImportError:
        _log.warning(
            "better_stack_token_set_but_logtail_not_installed",
            hint="install the 'observability' extra (logtail-python) to ship logs",
        )
        return
    try:
        logging.getLogger().addHandler(LogtailHandler(source_token=token))
        _log.info("better_stack_log_drain_enabled")
    except Exception as exc:  # never let log shipping break the app's boot
        _log.warning("better_stack_handler_init_failed", error=str(exc))


class TraceIDMiddleware:
    """ASGI middleware that binds a per-request trace_id and logs completion."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        inbound = headers.get(b"x-request-id") or headers.get(b"x-trace-id")
        trace_id = inbound.decode("latin-1") if inbound else uuid.uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            method=scope.get("method"),
            path=scope.get("path"),
        )
        start = time.perf_counter()
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])  # type: ignore[arg-type]
                raw_headers = list(message.get("headers") or [])
                raw_headers.append((b"x-request-id", trace_id.encode("latin-1")))
                message["headers"] = raw_headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            _log.exception("request_failed")
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            _log.info("request_completed", status_code=status_code, duration_ms=elapsed_ms)
            structlog.contextvars.clear_contextvars()
