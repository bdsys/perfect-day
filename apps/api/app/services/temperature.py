"""Temperature in the diary owner's unit for the AI prompt and its fact check.

Weather is stored in degrees Celsius. The model is shown whole degrees in the
owner's unit, rounded half up, so it never converts or rounds anything itself.
"""

from __future__ import annotations

import json
import math
from typing import Literal

TemperatureUnit = Literal["F", "C"]


def round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def to_unit(celsius: float, unit: TemperatureUnit) -> int:
    if unit == "F":
        return round_half_up(celsius * 9 / 5 + 32)
    return round_half_up(celsius)


def _convert(value: object, unit: TemperatureUnit) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return to_unit(float(value), unit)


def weather_for_prompt(payload: dict, unit: TemperatureUnit) -> dict:
    out = {k: v for k, v in payload.items() if k not in ("temperature_max_c", "temperature_min_c")}
    out["temperature_max"] = _convert(payload.get("temperature_max_c"), unit)
    out["temperature_min"] = _convert(payload.get("temperature_min_c"), unit)
    out["temperature_unit"] = "°F" if unit == "F" else "°C"
    return out


def _weather_json(payload: dict, unit: TemperatureUnit) -> str:
    return json.dumps(weather_for_prompt(payload, unit), ensure_ascii=False)


def enrichment_line(kind: str, payload: dict, unit: TemperatureUnit) -> str:
    if kind == "weather":
        return f"[weather] {_weather_json(payload, unit)}"
    return f"[{kind}] {json.dumps(payload)}"


def enrichment_source_text(enrichments: list, unit: TemperatureUnit) -> str:
    return " ".join(
        _weather_json(e.payload, unit)
        if getattr(e, "kind", None) == "weather"
        else json.dumps(e.payload)
        for e in enrichments
    )
