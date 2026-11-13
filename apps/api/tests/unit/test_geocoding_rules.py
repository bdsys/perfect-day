"""The strict rules that turn calendar location text into a city or ZIP search.

A wrong place is worse than none (it shows wrong weather), so anything
unclear must give no match. The shapes come from the 2026-11-12 test on real
calendar locations; see the item 24 spec.
"""

from __future__ import annotations

import pytest

from app.services.geocoding import parse_us_location


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Woodinville, WA 98072, USA", ("zip", "98072", "Washington")),
        (
            "Mukilteo Library, 4675 Harbour Pointe Blvd, Mukilteo, WA",
            ("city", "Mukilteo", "Washington"),
        ),
        # A house number is never read as a ZIP.
        ("12345 Main St, Everett, WA", ("city", "Everett", "Washington")),
        ("White River Amphitheatre, Auburn, Washington", ("city", "Auburn", "Washington")),
        # Exactly "City, ST" (plan call 5).
        ("Seattle, WA", ("city", "Seattle", "Washington")),
        ("Canlis, 2576 Aurora Ave N, Seattle, WA 98109", ("zip", "98109", "Washington")),
        ("Canlis, Seattle, WA", ("city", "Seattle", "Washington")),
    ],
)
def test_text_that_names_a_us_place(text, expected):
    assert parse_us_location(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Nordstrom, 3200 184th St SW, Lynnwood (Alderwood)",  # no state
        "Disneyland Park",
        "Home",
        "10 Downing St, London, UK",
        "",
        "   ",
        "Zoom, https://example.zoom.us/j/123",
    ],
)
def test_text_without_a_clear_us_place_gives_no_match(text):
    assert parse_us_location(text) is None
