"""Migration 0020: users.temperature_unit and the avatar columns."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AVATAR_ICONS, User


def _user(**kwargs) -> User:
    return User(email=f"{uuid.uuid4()}@example.com", **kwargs)


async def test_new_user_defaults_to_fahrenheit_and_initial(db_session: AsyncSession):
    user = _user()
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    assert user.temperature_unit == "F"
    assert user.avatar_icon is None
    assert user.avatar_s3_key is None
    assert user.avatar_dek_ciphertext is None


async def test_unit_must_be_f_or_c(db_session: AsyncSession):
    db_session.add(_user(temperature_unit="K"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_icon_must_be_one_of_the_16(db_session: AsyncSession):
    db_session.add(_user(avatar_icon="unicorn"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_every_listed_icon_is_accepted(db_session: AsyncSession):
    for name in AVATAR_ICONS:
        db_session.add(_user(avatar_icon=name))
    await db_session.commit()


async def test_icon_and_photo_are_exclusive(db_session: AsyncSession):
    db_session.add(_user(avatar_icon="cat", avatar_s3_key="avatars/x"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


def test_avatar_icons_order():
    assert AVATAR_ICONS == (
        "cat", "dog", "bird", "fish", "rabbit", "squirrel", "turtle", "panda",
        "snail", "flower", "sun", "moon", "rocket", "rainbow", "cake", "paw-print",
    )  # fmt: skip
