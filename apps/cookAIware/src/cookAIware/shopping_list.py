from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from cookAIware.data_store import load_json, save_json
from cookAIware.inventory import available_quantity, unit_for_item
from cookAIware.units import format_quantity


SHOPPING_LIST_FILE = "shopping_list.json"


def _list_path(data_dir: Path) -> Path:
    return data_dir / SHOPPING_LIST_FILE


def load_list(data_dir: Path) -> List[Dict[str, Any]]:
    return load_json(_list_path(data_dir), [])


def save_list(data_dir: Path, items: List[Dict[str, Any]]) -> None:
    save_json(_list_path(data_dir), items)


def list_items(data_dir: Path) -> List[Dict[str, Any]]:
    items = load_list(data_dir)
    items.sort(key=lambda item: item.get("name", ""))
    return items


def add_items(data_dir: Path, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    current = load_list(data_dir)
    for item in items:
        name = str(item.get("name", "")).strip().lower()
        if not name:
            continue
        qty = float(item.get("quantity", 1.0))
        unit = item.get("unit") or "pcs"
        merged = False
        for existing in current:
            if existing.get("name") == name and existing.get("unit") == unit:
                existing["quantity"] = float(existing.get("quantity", 0.0)) + qty
                merged = True
                break
        if not merged:
            current.append({"name": name, "quantity": qty, "unit": unit})
    save_list(data_dir, current)
    return current


def remove_items(data_dir: Path, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    current = load_list(data_dir)
    for item in items:
        name = str(item.get("name", "")).strip().lower()
        if not name:
            continue
        unit = item.get("unit")
        current = [
            entry
            for entry in current
            if not (entry.get("name") == name and (unit is None or entry.get("unit") == unit))
        ]
    save_list(data_dir, current)
    return current


def clear_list(data_dir: Path) -> None:
    save_list(data_dir, [])


def format_list(items: List[Dict[str, Any]]) -> List[str]:
    output: List[str] = []
    for item in items:
        qty = float(item.get("quantity", 0.0))
        unit = item.get("unit", "pcs")
        name = item.get("name", "")
        required = item.get("required_quantity")
        available = item.get("available_quantity")
        if required is not None and available is not None:
            output.append(
                f"{name} - need {format_quantity(float(required), unit)} (have {format_quantity(float(available), unit)})"
            )
        else:
            output.append(f"{name} - {format_quantity(qty, unit)}")
    return output


def generate_from_plan(
    data_dir: Path,
    plan: Dict[str, Any],
    inventory: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not plan:
        return []
    required: Dict[Tuple[str, str], float] = {}

    for day in plan.get("days", []):
        for meal in day.get("meals", []):
            for ing in meal.get("ingredients", []):
                name = ing.get("name")
                if not name:
                    continue
                unit = ing.get("unit") or unit_for_item(inventory, name) or "pcs"
                qty = float(ing.get("quantity", 0.0))
                key = (name, unit)
                required[key] = required.get(key, 0.0) + qty

    shopping: List[Dict[str, Any]] = []
    for (name, unit), qty in required.items():
        available = available_quantity(inventory, name, unit)
        shortfall = max(0.0, qty - available)
        if shortfall > 0:
            shopping.append(
                {
                    "name": name,
                    "quantity": shortfall,
                    "unit": unit,
                    "required_quantity": qty,
                    "available_quantity": available,
                    "shortfall_quantity": shortfall,
                }
            )

    save_list(data_dir, shopping)
    return shopping
