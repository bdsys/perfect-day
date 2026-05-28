"""Photo upload, encryption, retrieval, and attachment endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User

router = APIRouter(tags=["photos"])


class UploadUrlRequest(BaseModel):
    declared_mime: str
    declared_size: int


class UploadUrlResponse(BaseModel):
    photo_id: uuid.UUID
    upload_url: str
    upload_key: str
    expires_in: int
    required_headers: dict[str, str]


class PhotoOut(BaseModel):
    id: uuid.UUID
    mime_type: str | None
    bytes: int | None
    taken_at: datetime | None
    lat: float | None
    lon: float | None
    source: str
    finalized_at: datetime | None
    created_at: datetime
    deleted_at: datetime | None
    has_thumbnail: bool

    model_config = ConfigDict(from_attributes=True)


class PhotoAttachRequest(BaseModel):
    photo_id: uuid.UUID
    position: int | None = None


def _photo_out(photo) -> PhotoOut:
    return PhotoOut(
        id=photo.id,
        mime_type=photo.mime_type,
        bytes=photo.bytes,
        taken_at=photo.taken_at,
        lat=float(photo.lat) if photo.lat is not None else None,
        lon=float(photo.lon) if photo.lon is not None else None,
        source=photo.source,
        finalized_at=photo.finalized_at,
        created_at=photo.created_at,
        deleted_at=photo.deleted_at,
        has_thumbnail=photo.thumbnail_s3_key is not None,
    )


@router.post("/photos/upload-url", response_model=UploadUrlResponse)
async def request_upload_url(
    body: UploadUrlRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadUrlResponse:
    raise HTTPException(status_code=501, detail="Not implemented")
