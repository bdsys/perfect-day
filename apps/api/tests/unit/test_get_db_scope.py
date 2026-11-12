"""D-8 guard: every endpoint's database session is committed before the response.

`Depends(get_db)` without scope="function" commits after the response is
sent. See tests/integration/test_commit_before_response.py for the behaviour.
"""

from __future__ import annotations

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
UNSCOPED = re.compile(r"Depends\(\s*get_db\s*\)")


def test_no_endpoint_uses_get_db_without_function_scope():
    offenders = [
        f"{path.relative_to(APP.parent)}:{n}"
        for path in APP.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if UNSCOPED.search(line)
    ]
    assert offenders == [], 'use Depends(get_db, scope="function") at: ' + ", ".join(offenders)
