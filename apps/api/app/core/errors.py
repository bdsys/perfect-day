from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import HTTPException, RequestValidationError
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


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    http_exc = exc if isinstance(exc, HTTPException) else HTTPException(500)
    extra_headers = http_exc.headers or {}
    # Pass structured dict details through as-is (e.g. tier_limit, diary-create pattern).
    # Only wrap plain string details in the error envelope.
    if isinstance(http_exc.detail, dict):
        return JSONResponse(
            status_code=http_exc.status_code,
            content={"detail": http_exc.detail},
            headers=extra_headers,
        )
    resp = error_response(
        code="http_error",
        message=http_exc.detail if isinstance(http_exc.detail, str) else str(http_exc.detail),
        status_code=http_exc.status_code,
    )
    resp.headers.update(extra_headers)
    return resp


async def request_validation_handler(request: Request, exc: Exception) -> JSONResponse:
    errors = []
    if isinstance(exc, RequestValidationError):
        for e in exc.errors():
            # pydantic v2 puts the original exception object in ctx — strip url and convert to strings
            ctx = {k: str(v) for k, v in e.get("ctx", {}).items()} if e.get("ctx") else None
            entry = {k: v for k, v in e.items() if k not in ("ctx", "url")}
            if ctx:
                entry["ctx"] = ctx
            errors.append(entry)
    return error_response(
        code="validation_error",
        message="Request validation failed",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
