from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.get("/readyz")
async def readiness() -> JSONResponse:
    from app.core.config import get_settings
    from app.core.database import get_engine
    from app.core.dependencies import get_redis

    checks: dict[str, str] = {}
    healthy = True

    # Postgres
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"error: {e}"
        healthy = False

    # Redis
    try:
        r = get_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        healthy = False

    # MinIO / S3
    try:
        import asyncio
        import concurrent.futures

        from app.core.dependencies import get_s3

        settings = get_settings()

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            await loop.run_in_executor(
                pool,
                lambda: get_s3().head_bucket(Bucket=settings.s3_bucket_photos),
            )
        checks["minio"] = "ok"
    except Exception as e:
        checks["minio"] = f"error: {e}"
        # MinIO failure is warning-level — don't fail readiness for photo storage unavailability
        # on startup (bucket may not exist yet before first upload)

    status_code = 200 if healthy else 503
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks}, status_code=status_code
    )
