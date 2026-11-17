"""Drafts are queued only through enqueue_entry_draft, so every one gets a job row (row 38)."""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


class _Finder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: list[tuple[str]] = []
        self._stack: list[str] = []

    def _scope(self, node) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _scope

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr in ("delay", "apply_async")
            and isinstance(f.value, ast.Name)
            and f.value.id == "generate_entry_draft"
        ):
            self.found.append((self._stack[-1] if self._stack else "<module>",))
        self.generic_visit(node)


def test_drafts_are_queued_only_through_enqueue_entry_draft():
    calls: list[tuple[str, str]] = []
    for path in APP.rglob("*.py"):
        finder = _Finder()
        finder.visit(ast.parse(path.read_text()))
        calls += [(path.relative_to(APP).as_posix(), fn) for (fn,) in finder.found]

    assert calls, "no generate_entry_draft.delay/apply_async calls found: is the guard stale?"
    stray = [c for c in calls if c != ("workers/tasks.py", "enqueue_entry_draft")]
    assert stray == []
