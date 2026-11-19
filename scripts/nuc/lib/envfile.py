#!/usr/bin/env python3
"""Read and change one setting in an env file (KEY=value lines), safely.

Usage:
  envfile.py get <file> <KEY>             print the value (exit 3 absent, 4 duplicated)
  envfile.py set <file> <KEY> <VALUE>     replace the live line, or append one
  envfile.py ensure <file> <KEY> hex32    add a generated value only if KEY is absent
  envfile.py unset <file> <KEY>           remove the live line (no-op if absent)

Every change writes through symlinks to the real file (on the NUC, ./.env and
apps/api/.env are symlinks to /etc/perfect-day/app.env; replacing a symlink with
a plain file would split the config), keeps its mode and owner, and first copies
it to <real file>.bak-<UTC timestamp>. Commented lines are never touched.

`ensure` never prints the value: it says "KEY generated" or "KEY kept". `get` is
for settings that are not secret (the storage address); scripts that read a
secret with it keep the value in a variable and never echo it.

Tracker row 41 (the Garage switch). Standard library only: runs on the NUC host.
"""

from __future__ import annotations

import os
import secrets
import shutil
import sys
from datetime import UTC, datetime

GENERATORS = {"hex32": lambda: secrets.token_hex(32)}


def _live_indexes(lines: list[str], key: str) -> list[int]:
    return [
        i
        for i, line in enumerate(lines)
        if not line.lstrip().startswith("#") and line.split("=", 1)[0].strip() == key
    ]


def _read(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def _write(path: str, lines: list[str]) -> str:
    """Replace the real file's content, keeping mode and owner; return the backup path."""
    real = os.path.realpath(path)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = f"{real}.bak-{stamp}"
    shutil.copy2(real, backup)  # copy2 keeps the mode
    st = os.stat(real)
    tmp = f"{real}.tmp-{stamp}"
    # Created 0600 so the content is never readable by others, even for a moment.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass  # not root: the owner is already ours
        os.replace(tmp, real)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return backup


def cmd_get(path: str, key: str) -> int:
    lines = _read(path)
    idx = _live_indexes(lines, key)
    if not idx:
        print(f"{key} is not set in {path}", file=sys.stderr)
        return 3
    if len(idx) > 1:
        print(f"{key} is set {len(idx)} times in {path}; fix the file by hand", file=sys.stderr)
        return 4
    print(lines[idx[0]].split("=", 1)[1])
    return 0


def cmd_set(path: str, key: str, value: str) -> int:
    lines = _read(path)
    idx = _live_indexes(lines, key)
    if len(idx) > 1:
        print(f"{key} is set {len(idx)} times in {path}; nothing changed", file=sys.stderr)
        return 4
    if idx:
        lines[idx[0]] = f"{key}={value}"
    else:
        lines.append(f"{key}={value}")
    _write(path, lines)
    print(f"{key} set in {os.path.realpath(path)}")
    return 0


def cmd_ensure(path: str, key: str, generator: str) -> int:
    if generator not in GENERATORS:
        print(f"unknown generator {generator!r}; known: {', '.join(GENERATORS)}", file=sys.stderr)
        return 2
    lines = _read(path)
    idx = _live_indexes(lines, key)
    if idx:
        print(f"{key} kept")
        return 0
    lines.append(f"{key}={GENERATORS[generator]()}")
    _write(path, lines)
    print(f"{key} generated")
    return 0


def cmd_unset(path: str, key: str) -> int:
    lines = _read(path)
    idx = _live_indexes(lines, key)
    if not idx:
        print(f"{key} was not set")
        return 0
    if len(idx) > 1:
        print(f"{key} is set {len(idx)} times in {path}; nothing changed", file=sys.stderr)
        return 4
    del lines[idx[0]]
    _write(path, lines)
    print(f"{key} removed from {os.path.realpath(path)}")
    return 0


def main(argv: list[str]) -> int:
    commands = {
        "get": (cmd_get, 2),
        "set": (cmd_set, 3),
        "ensure": (cmd_ensure, 3),
        "unset": (cmd_unset, 2),
    }
    if len(argv) < 2 or argv[1] not in commands or len(argv) - 2 != commands[argv[1]][1]:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    func, _ = commands[argv[1]]
    if not os.path.isfile(argv[2]):
        print(f"{argv[2]}: no such file", file=sys.stderr)
        return 2
    return func(*argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
