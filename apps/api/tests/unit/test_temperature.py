"""Unit tests: temperature conversion for the AI prompt."""

from __future__ import annotations

import pytest

from app.services.temperature import (
    enrichment_line,
    to_unit,
    weather_for_prompt,
)


@pytest.mark.parametrize(
    ("c", "unit", "want"),
    [
        (21.5, "F", 71),
        (-17.5, "F", 1),
        (20.5, "C", 21),
        (-0.5, "C", 0),
        (-1.5, "C", -1),
        (16.0, "F", 61),
    ],
)
def test_to_unit_rounds_half_up(c, unit, want):
    assert to_unit(c, unit) == want


def test_weather_for_prompt_replaces_celsius_keys():
    out = weather_for_prompt(
        {"temperature_max_c": 21.5, "temperature_min_c": 16.0, "condition": "sunny"}, "F"
    )
    assert out == {
        "temperature_max": 71,
        "temperature_min": 61,
        "temperature_unit": "°F",
        "condition": "sunny",
    }


def test_weather_for_prompt_missing_values_are_none():
    assert weather_for_prompt({"temperature_max_c": None}, "C")["temperature_max"] is None


def test_weather_line_uses_symbol_not_escape():
    line = enrichment_line("weather", {"temperature_max_c": 21.5}, "F")
    assert '"temperature_unit": "°F"' in line
    assert "\\u00b0" not in line
    assert "Fahrenheit" not in line


def test_other_kinds_unchanged():
    assert enrichment_line("place", {"name": "Zoo"}, "F") == '[place] {"name": "Zoo"}'
