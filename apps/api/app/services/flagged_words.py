"""Flagged words: the "verify before publishing" warnings on a draft (follow-up 25.3).

A flagged word stays on an entry until it is no longer in the text or the
entry is published.
"""

from __future__ import annotations

import re


def still_present(words: list[str], text: str) -> list[str]:
    """The *words* that still appear in *text* as whole words, in order, without duplicates."""
    kept: list[str] = []
    for word in words:
        if word in kept:
            continue
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text):
            kept.append(word)
    return kept
