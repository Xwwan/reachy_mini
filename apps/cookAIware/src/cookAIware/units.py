from __future__ import annotations

from typing import Tuple


UNIT_ALIASES = {
    "g": ("g", 1.0),
    "gram": ("g", 1.0),
    "grams": ("g", 1.0),
    "kg": ("g", 1000.0),
    "kilogram": ("g", 1000.0),
    "kilograms": ("g", 1000.0),
    "ml": ("ml", 1.0),
    "milliliter": ("ml", 1.0),
    "milliliters": ("ml", 1.0),
    "l": ("ml", 1000.0),
    "liter": ("ml", 1000.0),
    "liters": ("ml", 1000.0),
    "pcs": ("pcs", 1.0),
    "pc": ("pcs", 1.0),
    "piece": ("pcs", 1.0),
    "pieces": ("pcs", 1.0),
    "unit": ("pcs", 1.0),
    "units": ("pcs", 1.0),
}


def normalize_unit(unit: str | None) -> Tuple[str, float] | None:
    if not unit:
        return None
    key = unit.strip().lower()
    return UNIT_ALIASES.get(key)


def normalize_quantity(quantity: float | int | str | None, unit: str | None) -> Tuple[float, str] | None:
    if quantity is None:
        return None
    try:
        qty = float(quantity)
    except Exception:
        return None
    if qty <= 0:
        return None
    normalized = normalize_unit(unit)
    if not normalized:
        return None
    base_unit, factor = normalized
    return qty * factor, base_unit


def format_quantity(quantity: float, unit: str) -> str:
    if unit == "g" and quantity >= 1000:
        return f"{quantity / 1000:.2f} kg"
    if unit == "ml" and quantity >= 1000:
        return f"{quantity / 1000:.2f} l"
    if unit == "pcs":
        if abs(quantity - round(quantity)) < 0.001:
            return f"{int(round(quantity))} pcs"
    return f"{quantity:.2f} {unit}"
