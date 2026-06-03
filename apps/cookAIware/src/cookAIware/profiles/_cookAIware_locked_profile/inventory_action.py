from __future__ import annotations

from typing import Any, Dict, List

from cookAIware.tools.core_tools import Tool, ToolDependencies
from cookAIware.inventory import add_items, cook_items, list_inventory, format_inventory, normalize_for_display


class InventoryActionTool(Tool):
    name = "inventory_action"
    description = "Add food, cook food, or list inventory items."
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "cook", "list"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "quantity": {"type": "number"},
                        "unit": {"type": "string"},
                        "expiration_date": {"type": "string"},
                        "storage_location": {"type": "string"},
                    },
                },
            },
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        if deps.data_dir is None:
            return {"status": "error", "message": "Data directory not available."}

        action = kwargs.get("action")
        items = kwargs.get("items") or []

        if action == "list":
            inventory = list_inventory(deps.data_dir)
            return {
                "status": "ok",
                "message": "Inventory listed.",
                "items": normalize_for_display(inventory),
                "lines": format_inventory(inventory),
            }

        if action in ("add", "cook") and not items:
            return {
                "status": "needs_clarification",
                "message": "Which items?",
                "missing": ["items"],
            }

        missing_fields: List[str] = []
        for item in items:
            if not item.get("name"):
                missing_fields.append("name")
            if action == "add":
                if item.get("quantity") is None:
                    missing_fields.append("quantity")
                if not item.get("unit"):
                    missing_fields.append("unit")
            if action == "cook":
                if item.get("quantity") is None:
                    missing_fields.append("quantity")
                if not item.get("unit"):
                    missing_fields.append("unit")

        if missing_fields:
            return {
                "status": "needs_clarification",
                "message": "Missing details.",
                "missing": sorted(set(missing_fields)),
            }

        if action == "add":
            inventory, errors = add_items(deps.data_dir, items)
            return {
                "status": "ok" if not errors else "partial",
                "message": "Items added." if not errors else "Some items need attention.",
                "errors": errors,
                "items": normalize_for_display(inventory),
            }

        if action == "cook":
            inventory, errors = cook_items(deps.data_dir, items)
            return {
                "status": "ok" if not errors else "partial",
                "message": "Inventory updated." if not errors else "Some items need attention.",
                "errors": errors,
                "items": normalize_for_display(inventory),
            }

        return {"status": "error", "message": "Unknown action."}
