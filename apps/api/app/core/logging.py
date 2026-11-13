from __future__ import annotations

import logging

import structlog
import structlog.stdlib
from structlog.types import Processor

# httpx logs every request URL at INFO. Google Photos base URLs grant access to
# a photo for an hour and picker session ids sit in request paths, so these
# loggers only speak up at WARNING, in the API and in the worker.
QUIET_HTTP_LOGGERS = ("httpx", "httpcore")


def quiet_http_loggers() -> None:
    for name in QUIET_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging(env: str = "dev") -> None:
    quiet_http_loggers()
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if env == "dev":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
