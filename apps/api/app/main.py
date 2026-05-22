from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import get_settings
from app.core.errors import http_exception_handler, request_validation_handler, unhandled_exception_handler
from app.core.logging import configure_logging
from app.middleware.rate_limit import limiter
from app.middleware.request_id import RequestIDMiddleware
from app.routers import health


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.env)

    app = FastAPI(
        title="Perfect Day API",
        version="0.1.0",
        docs_url="/docs" if settings.env == "dev" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.env == "dev" else None,
    )

    # Rate limiter state
    app.state.limiter = limiter

    # Middleware (outermost first)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception handlers
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # Routers
    app.include_router(health.router)

    # v1 routers (imported lazily to keep startup order explicit)
    from app.routers.v1 import auth, diaries, entries, integrations, scan

    app.include_router(auth.router, prefix="/v1")
    app.include_router(diaries.router, prefix="/v1")
    app.include_router(entries.router, prefix="/v1")
    app.include_router(integrations.router, prefix="/v1")
    app.include_router(scan.router, prefix="/v1")

    return app


app = create_app()
