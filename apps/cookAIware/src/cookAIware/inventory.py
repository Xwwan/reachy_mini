from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cookAIware.data_store import load_json, save_json
from cookAIware.units import normalize_quantity, normalize_unit, format_quantity


INVENTORY_FILE = "inventory.json"


@dataclass
class InventoryItem:
    name: str
    quantity: float
    unit: str
    expiration_date: str | None
    storage_location: str | None


def _inventory_path(data_dir: Path) -> Path:
    return data_dir / INVENTORY_FILE


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _parse_date(date_str: str | None) -> str | None:
    if not date_str:
        return None
    try:
        parsed = datetime.fromisoformat(date_str.strip()).date()
        return parsed.isoformat()
    except Exception:
        return None


def load_inventory(data_dir: Path) -> List[Dict[str, Any]]:
    return load_json(_inventory_path(data_dir), [])


def save_inventory(data_dir: Path, items: List[Dict[str, Any]]) -> None:
    save_json(_inventory_path(data_dir), items)


def add_items(data_dir: Path, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    inventory = load_inventory(data_dir)
    errors: List[str] = []

    for item in items:
        raw_name = str(item.get("name", "")).strip()
        name = _normalize_name(raw_name)
        if not name:
            errors.append("missing_name")
            continue

        expiration_date = _parse_date(item.get("expiration_date"))
        if item.get("expiration_date") and not expiration_date:
            errors.append(f"invalid_expiration_date:{name}")
            continue

        storage_location = item.get("storage_location")
        if storage_location is not None:
            storage_location = str(storage_location).strip() or None

        normalized = normalize_quantity(item.get("quantity"), item.get("unit"))
        if not normalized:
            errors.append(f"invalid_quantity_or_unit:{name}")
            continue
        quantity, unit = normalized

        merged = False
        for inv in inventory:
            if (
                inv.get("name") == name
                and inv.get("unit") == unit
                and inv.get("expiration_date") == expiration_date
                and inv.get("storage_location") == storage_location
            ):
                inv["quantity"] = float(inv.get("quantity", 0.0)) + quantity
                if raw_name:
                    inv["display_name"] = raw_name
                merged = True
                break

        if not merged:
            inventory.append(
                {
                    "name": name,
                    "display_name": raw_name or name,
                    "quantity": quantity,
                    "unit": unit,
                    "expiration_date": expiration_date,
                    "storage_location": storage_location,
                }
            )

    save_inventory(data_dir, inventory)
    return inventory, errors


def cook_items(data_dir: Path, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    inventory = load_inventory(data_dir)
    errors: List[str] = []

    for item in items:
        name = _normalize_name(str(item.get("name", "")))
        if not name:
            errors.append("missing_name")
            continue

        normalized = normalize_quantity(item.get("quantity"), item.get("unit"))
        if not normalized:
            errors.append(f"invalid_quantity_or_unit:{name}")
            continue
        quantity_needed, unit = normalized

        matching = [
            inv
            for inv in inventory
            if inv.get("name") == name and inv.get("unit") == unit and float(inv.get("quantity", 0.0)) > 0
        ]
        if not matching:
            errors.append(f"not_found:{name}")
            continue

        matching.sort(key=lambda inv: inv.get("expiration_date") or "9999-12-31")
        remaining = quantity_needed
        for inv in matching:
            available = float(inv.get("quantity", 0.0))
            if available <= 0:
                continue
            take = min(available, remaining)
            inv["quantity"] = available - take
            remaining -= take
            if remaining <= 0:
                break

        if remaining > 0:
            errors.append(f"insufficient_quantity:{name}")

    save_inventory(data_dir, inventory)
    return inventory, errors


def list_inventory(data_dir: Path) -> List[Dict[str, Any]]:
    inventory = load_inventory(data_dir)
    inventory.sort(key=lambda inv: (inv.get("expiration_date") or "9999-12-31", inv.get("name", "")))
    return inventory


def format_inventory(items: List[Dict[str, Any]]) -> List[str]:
    output: List[str] = []
    for item in items:
        qty = float(item.get("quantity", 0.0))
        unit = item.get("unit", "")
        label = item.get("display_name") or item.get("name", "")
        exp = item.get("expiration_date")
        loc = item.get("storage_location")
        parts = [label, format_quantity(qty, unit)]
        if exp:
            parts.append(f"exp {exp}")
        if loc:
            parts.append(f"stored {loc}")
        output.append(" - ".join(parts))
    return output


def available_quantity(items: List[Dict[str, Any]], name: str, unit: str) -> float:
    total = 0.0
    for item in items:
        if item.get("name") == name and item.get("unit") == unit:
            total += float(item.get("quantity", 0.0))
    return total


def unit_for_item(items: List[Dict[str, Any]], name: str) -> str | None:
    for item in items:
        if item.get("name") == name:
            return item.get("unit")
    return None


def normalize_for_display(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in items:
        unit = item.get("unit")
        quantity = float(item.get("quantity", 0.0))
        display_qty = format_quantity(quantity, unit) if unit else str(quantity)
        result.append(
            {
                **item,
                "display_name": item.get("display_name") or item.get("name"),
                "display_quantity": display_qty,
            }
        )
    return result


def normalize_items_for_save(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized_items: List[Dict[str, Any]] = []
    for item in items:
        raw_name = str(item.get("name", "")).strip()
        name = _normalize_name(raw_name)
        if not name:
            continue
        expiration_date = _parse_date(item.get("expiration_date"))
        storage_location = item.get("storage_location")
        if storage_location is not None:
            storage_location = str(storage_location).strip() or None
        normalized = normalize_quantity(item.get("quantity"), item.get("unit"))
        if not normalized:
            continue
        quantity, unit = normalized
        normalized_items.append(
            {
                "name": name,
                "display_name": raw_name or name,
                "quantity": quantity,
                "unit": unit,
                "expiration_date": expiration_date,
                "storage_location": storage_location,
            }
        )
    return normalized_items
