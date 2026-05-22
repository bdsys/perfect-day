from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


def error_response(
    code: str,
    message: str,
    status_code: int,
    details: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message, **({"details": details} if details else {})}
        },
    )


async def http_exception_handler(request: Request, exc) -> JSONResponse:

    return error_response(
        code="http_error",
        message=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        status_code=exc.status_code,
    )


async def request_validation_handler(request: Request, exc) -> JSONResponse:
    errors = []
    for e in exc.errors(include_url=False):
        # pydantic v2 puts the original exception object in ctx — strip it to keep JSON-serializable
        ctx = {k: str(v) for k, v in e.get("ctx", {}).items()} if e.get("ctx") else None
        errors.append({**{k: v for k, v in e.items() if k != "ctx"}, **({"ctx": ctx} if ctx else {})})
    return error_response(
        code="validation_error",
        message="Request validation failed",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details={"errors": errors},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    import structlog

    log = structlog.get_logger()
    log.error("unhandled_exception", exc=exc, path=request.url.path)
    return error_response(
        code="internal_error",
        message="An unexpected error occurred",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
