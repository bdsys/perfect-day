"""Schema for photo display copies, per-user Google duplicates and Google imports (item 14)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.factories import make_diary, make_entry, make_photo, make_user


async def _google_photo(db: AsyncSession, user, external_id: str):
    photo = await make_photo(db, user=user, source="google_photos")
    photo.external_id = external_id
    await db.flush()
    return photo


class TestPhotoImportsSchema:
    async def test_photo_has_display_key(self, db_session: AsyncSession):
        user = await make_user(db_session)
        photo = await make_photo(db_session, user=user)
        photo.display_s3_key = f"{user.id}/{photo.id}_display.enc"
        await db_session.commit()

        stored = (
            await db_session.execute(
                text("SELECT display_s3_key FROM photos WHERE id = :id"), {"id": photo.id}
            )
        ).scalar_one()
        assert stored == f"{user.id}/{photo.id}_display.enc"

    async def test_photo_imports_status_check_rejects_unknown(self, db_session: AsyncSession):
        user = await make_user(db_session)
        entry = await make_entry(db_session, await make_diary(db_session, owner=user))
        await db_session.commit()

        with pytest.raises(IntegrityError):
            await db_session.execute(
                text(
                    "INSERT INTO photo_imports (id, user_id, entry_id, google_session_id, status)"
                    " VALUES (gen_random_uuid(), :u, :e, 's1', 'bogus')"
                ),
                {"u": user.id, "e": entry.id},
            )
        await db_session.rollback()

    async def test_photo_imports_defaults(self, db_session: AsyncSession):
        user = await make_user(db_session)
        entry = await make_entry(db_session, await make_diary(db_session, owner=user))
        await db_session.commit()

        row = (
            await db_session.execute(
                text(
                    "INSERT INTO photo_imports (id, user_id, entry_id, google_session_id)"
                    " VALUES (gen_random_uuid(), :u, :e, 's1')"
                    " RETURNING status, added_count, skipped_videos, failed_names, dismissed_at"
                ),
                {"u": user.id, "e": entry.id},
            )
        ).one()
        assert row == ("waiting", 0, 0, [], None)
        await db_session.rollback()

    async def test_same_google_item_allowed_for_two_users(self, db_session: AsyncSession):
        first = await make_user(db_session)
        second = await make_user(db_session)
        await _google_photo(db_session, first, "g-1")
        await _google_photo(db_session, second, "g-1")
        await db_session.commit()

    async def test_same_google_item_twice_for_one_user_rejected(self, db_session: AsyncSession):
        user = await make_user(db_session)
        await _google_photo(db_session, user, "g-1")
        await db_session.commit()

        with pytest.raises(IntegrityError):
            await _google_photo(db_session, user, "g-1")
        await db_session.rollback()

    async def test_deleted_duplicate_does_not_block_new_copy(self, db_session: AsyncSession):
        user = await make_user(db_session)
        old = await _google_photo(db_session, user, "g-1")
        old.deleted_at = datetime.now(tz=UTC)
        await db_session.commit()

        await _google_photo(db_session, user, "g-1")
        await db_session.commit()
