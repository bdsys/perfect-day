#!/usr/bin/env python3
"""Admin CLI for Perfect Day — local dev use only.

Run via: ./scripts/admin.sh <command> [options]
"""
from __future__ import annotations

import re
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import click
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Allow importing from apps/api when run directly
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.core.security import hash_password  # noqa: E402
from app.models import Diary, Entry, ScanJob, User  # noqa: E402

# ---------------------------------------------------------------------------
# DB session
# ---------------------------------------------------------------------------

def _load_database_url() -> str:
    """Read DATABASE_URL_SYNC from apps/api/.env. Strips surrounding quotes."""
    env_file = REPO_ROOT / "apps" / "api" / ".env"
    if not env_file.exists():
        raise click.ClickException(f".env not found at {env_file}. Run `make bootstrap` first.")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("DATABASE_URL_SYNC="):
            value = line.split("=", 1)[1].strip()
            # Strip optional surrounding quotes
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            return value
    raise click.ClickException("DATABASE_URL_SYNC not found in apps/api/.env")


def _make_session() -> Session:
    url = _load_database_url()
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return factory()


def _confirm_destructive(message: str) -> None:
    click.echo(message)
    answer = input('Type "yes" to confirm: ').strip()
    if answer != "yes":
        click.echo("Aborted.")
        raise SystemExit(0)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "diary"


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------

@click.group()
def cli() -> None:
    """Perfect Day admin CLI — local dev use only."""


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

@cli.group()
def users() -> None:
    """Manage users."""


@users.command("list")
def users_list() -> None:
    """List all users."""
    with _make_session() as db:
        rows = db.query(User).order_by(User.created_at).all()
    if not rows:
        click.echo("No users found.")
        return
    click.echo(f"{'ID':<38}  {'EMAIL':<40}  {'TIER':<8}  {'ADMIN':<5}  {'DELETED'}")
    click.echo("-" * 110)
    for u in rows:
        deleted = u.deleted_at.strftime("%Y-%m-%d") if u.deleted_at else ""
        click.echo(
            f"{str(u.id):<38}  {u.email:<40}  {u.subscription_tier:<8}  "
            f"{str(u.is_admin):<5}  {deleted}"
        )


@users.command("create")
@click.option("--email", required=True, help="User email address.")
@click.option("--password", required=True, help="Plain-text password (will be hashed).")
@click.option("--tier", default="free", show_default=True,
              type=click.Choice(["free", "tier1", "tier2"]), help="Subscription tier.")
@click.option("--admin", is_flag=True, default=False, help="Grant admin flag.")
def users_create(email: str, password: str, tier: str, admin: bool) -> None:
    """Create a new user with email/password auth."""
    with _make_session() as db:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            raise click.ClickException(f"User with email {email!r} already exists.")
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=hash_password(password),
            subscription_tier=tier,
            is_admin=admin,
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    click.echo(f"Created user {user.email} (id={user.id}, tier={user.subscription_tier}).")


@users.command("delete")
@click.option("--email", required=True, help="Email of user to delete.")
@click.option("--hard", is_flag=True, default=False,
              help="Permanently delete row (DB FK cascades). Default is soft delete.")
def users_delete(email: str, hard: bool) -> None:
    """Delete a user. Soft by default (sets deleted_at and cascades to diaries/entries)."""
    with _make_session() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise click.ClickException(f"No user found with email {email!r}.")
        diary_count = db.query(Diary).filter(
            Diary.owner_user_id == user.id, Diary.deleted_at.is_(None)
        ).count()
        entry_count = (
            db.query(Entry)
            .join(Diary, Entry.diary_id == Diary.id)
            .filter(Diary.owner_user_id == user.id, Entry.deleted_at.is_(None))
            .count()
        )
        if hard:
            _confirm_destructive(
                f"\n⚠  HARD DELETE: This will PERMANENTLY remove user "
                f"{user.email} (id={user.id}).\n"
                f"DB cascades will also remove: {diary_count} diary/diaries, "
                f"{entry_count} entry/entries,\n"
                f"plus all related rows (oauth tokens, photos, scan jobs, "
                f"notifications, etc.).\nThis cannot be undone."
            )
            db.delete(user)
        else:
            _confirm_destructive(
                f"\nThis will soft-delete user {user.email} (id={user.id}) and cascade to:\n"
                f"  {diary_count} diary/diaries\n"
                f"  {entry_count} entry/entries\n"
                f"All marked with deleted_at; rows remain in the DB."
            )
            now = datetime.now(UTC)
            user.deleted_at = now
            diary_ids = [d.id for d in db.query(Diary).filter(Diary.owner_user_id == user.id).all()]
            db.query(Diary).filter(Diary.owner_user_id == user.id).update({"deleted_at": now})
            if diary_ids:
                db.query(Entry).filter(Entry.diary_id.in_(diary_ids)).update({"deleted_at": now})
        db.commit()
    click.echo(f"{'Hard-deleted' if hard else 'Soft-deleted'} user {email}.")


@users.command("set-tier")
@click.option("--email", required=True, help="Email of the user.")
@click.option("--tier", required=True, type=click.Choice(["free", "tier1", "tier2"]),
              help="New subscription tier.")
def users_set_tier(email: str, tier: str) -> None:
    """Change a user's subscription tier."""
    with _make_session() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise click.ClickException(f"No user found with email {email!r}.")
        old_tier = user.subscription_tier
        user.subscription_tier = tier
        db.commit()
    click.echo(f"Updated {email}: {old_tier} → {tier}.")


# ---------------------------------------------------------------------------
# diaries
# ---------------------------------------------------------------------------

@cli.group()
def diaries() -> None:
    """Manage diaries."""


@diaries.command("list")
@click.option("--email", required=True, help="Owner's email address.")
def diaries_list(email: str) -> None:
    """List all diaries owned by a user."""
    with _make_session() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise click.ClickException(f"No user found with email {email!r}.")
        rows = (
            db.query(Diary)
            .filter(Diary.owner_user_id == user.id)
            .order_by(Diary.created_at)
            .all()
        )
    if not rows:
        click.echo(f"No diaries found for {email}.")
        return
    click.echo(f"{'ID':<38}  {'NAME':<30}  {'SLUG':<20}  {'DELETED'}")
    click.echo("-" * 100)
    for d in rows:
        deleted = d.deleted_at.strftime("%Y-%m-%d") if d.deleted_at else ""
        click.echo(f"{str(d.id):<38}  {d.name:<30}  {d.slug:<20}  {deleted}")


@diaries.command("create")
@click.option("--email", required=True, help="Owner's email address.")
@click.option("--name", required=True, help="Display name for the diary.")
@click.option("--timezone", "tz", default="UTC", show_default=True,
              help="Diary timezone (e.g. America/New_York).")
def diaries_create(email: str, name: str, tz: str) -> None:
    """Create a new diary for a user (also creates 1:1 scan_job row)."""
    with _make_session() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise click.ClickException(f"No user found with email {email!r}.")
        slug = _slugify(name)
        existing = db.query(Diary).filter(
            Diary.owner_user_id == user.id, Diary.slug == slug
        ).first()
        if existing:
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"
        diary = Diary(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name=name,
            slug=slug,
            timezone=tz,
        )
        db.add(diary)
        db.flush()
        # Match API behavior — every diary needs a 1:1 scan_job
        db.add(ScanJob(diary_id=diary.id))
        db.commit()
        db.refresh(diary)
    click.echo(f"Created diary {diary.name!r} (id={diary.id}, slug={diary.slug}).")


@diaries.command("delete")
@click.option("--diary-id", required=True, help="UUID of the diary to delete.")
@click.option("--hard", is_flag=True, default=False,
              help="Permanently delete row (DB FK cascades). Default is soft delete.")
def diaries_delete(diary_id: str, hard: bool) -> None:
    """Delete a diary. Soft by default (sets deleted_at and cascades to entries)."""
    with _make_session() as db:
        try:
            did = uuid.UUID(diary_id)
        except ValueError:
            raise click.ClickException(f"Invalid UUID: {diary_id!r}")
        diary = db.query(Diary).filter(Diary.id == did).first()
        if not diary:
            raise click.ClickException(f"No diary found with id {diary_id!r}.")
        entry_count = db.query(Entry).filter(
            Entry.diary_id == did, Entry.deleted_at.is_(None)
        ).count()
        if hard:
            _confirm_destructive(
                f"\n⚠  HARD DELETE: This will PERMANENTLY remove diary "
                f"{diary.name!r} (id={diary.id}).\n"
                f"DB cascades will remove {entry_count} entry/entries plus scan_job, "
                f"permissions, photos, etc.\nThis cannot be undone."
            )
            db.delete(diary)
        else:
            _confirm_destructive(
                f"\nThis will soft-delete diary {diary.name!r} (id={diary.id}) and "
                f"{entry_count} entry/entries.\nAll marked with deleted_at; rows remain in the DB."
            )
            now = datetime.now(UTC)
            diary.deleted_at = now
            db.query(Entry).filter(Entry.diary_id == did).update({"deleted_at": now})
        db.commit()
    click.echo(f"{'Hard-deleted' if hard else 'Soft-deleted'} diary {diary_id}.")


# ---------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------

@cli.group()
def entries() -> None:
    """Manage entries."""


@entries.command("list")
@click.option("--diary-id", required=True, help="UUID of the diary.")
def entries_list(diary_id: str) -> None:
    """List all entries in a diary (including soft-deleted ones)."""
    with _make_session() as db:
        try:
            did = uuid.UUID(diary_id)
        except ValueError:
            raise click.ClickException(f"Invalid UUID: {diary_id!r}")
        diary = db.query(Diary).filter(Diary.id == did).first()
        if not diary:
            raise click.ClickException(f"No diary found with id {diary_id!r}.")
        rows = (
            db.query(Entry)
            .filter(Entry.diary_id == did)
            .order_by(Entry.entry_date.desc())
            .all()
        )
    if not rows:
        click.echo(f"No entries found in diary {diary_id}.")
        return
    click.echo(f"{'ID':<38}  {'DATE':<12}  {'STATUS':<10}  {'SOURCE':<8}  {'TITLE'}")
    click.echo("-" * 110)
    for e in rows:
        deleted_marker = " [deleted]" if e.deleted_at else ""
        title = (e.title or "")[:40]
        click.echo(
            f"{str(e.id):<38}  {str(e.entry_date):<12}  {e.status:<10}  "
            f"{e.body_source:<8}  {title}{deleted_marker}"
        )


@entries.command("create")
@click.option("--diary-id", required=True, help="UUID of the diary.")
@click.option("--date", "entry_date", required=True, help="Entry date (YYYY-MM-DD).")
@click.option("--title", default=None, help="Entry title.")
@click.option("--body", default=None, help="Entry body markdown.")
def entries_create(diary_id: str, entry_date: str, title: str | None, body: str | None) -> None:
    """Create a manual draft entry in a diary."""
    with _make_session() as db:
        try:
            did = uuid.UUID(diary_id)
        except ValueError:
            raise click.ClickException(f"Invalid UUID: {diary_id!r}")
        diary = db.query(Diary).filter(Diary.id == did).first()
        if not diary:
            raise click.ClickException(f"No diary found with id {diary_id!r}.")
        try:
            parsed_date = date.fromisoformat(entry_date)
        except ValueError:
            raise click.ClickException(f"Invalid date {entry_date!r}. Use YYYY-MM-DD format.")
        entry = Entry(
            id=uuid.uuid4(),
            diary_id=did,
            entry_date=parsed_date,
            title=title,
            body_markdown=body,
            status="draft",
            created_by="manual",
            body_source="manual",
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
    click.echo(f"Created entry {entry.id} (date={entry.entry_date}, status=draft).")


@entries.command("delete")
@click.option("--entry-id", required=True, help="UUID of the entry to delete.")
@click.option("--hard", is_flag=True, default=False,
              help="Permanently delete row (DB FK cascades). Default is soft delete.")
def entries_delete(entry_id: str, hard: bool) -> None:
    """Delete an entry. Soft by default (sets deleted_at)."""
    with _make_session() as db:
        try:
            eid = uuid.UUID(entry_id)
        except ValueError:
            raise click.ClickException(f"Invalid UUID: {entry_id!r}")
        entry = db.query(Entry).filter(Entry.id == eid).first()
        if not entry:
            raise click.ClickException(f"No entry found with id {entry_id!r}.")
        diary = db.query(Diary).filter(Diary.id == entry.diary_id).first()
        details = (
            f"  Diary: {diary.name if diary else 'unknown'}\n"
            f"  Date:  {entry.entry_date}\n"
            f"  Title: {entry.title or '(untitled)'}\n"
            f"  Status: {entry.status}"
        )
        if hard:
            _confirm_destructive(
                f"\n⚠  HARD DELETE: This will PERMANENTLY remove entry {entry_id}.\n"
                f"{details}\nThis cannot be undone."
            )
            db.delete(entry)
        else:
            _confirm_destructive(
                f"\nThis will soft-delete entry {entry_id}.\n{details}"
            )
            entry.deleted_at = datetime.now(UTC)
        db.commit()
    click.echo(f"{'Hard-deleted' if hard else 'Soft-deleted'} entry {entry_id}.")


if __name__ == "__main__":
    cli()
