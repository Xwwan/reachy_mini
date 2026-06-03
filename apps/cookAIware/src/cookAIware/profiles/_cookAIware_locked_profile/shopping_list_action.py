from __future__ import annotations

from typing import Any, Dict

from cookAIware.tools.core_tools import Tool, ToolDependencies
from cookAIware.inventory import list_inventory
from cookAIware.meal_planner import load_plan
from cookAIware.shopping_list import (
    generate_from_plan,
    list_items,
    add_items,
    remove_items,
    clear_list,
    format_list,
)


class ShoppingListActionTool(Tool):
    name = "shopping_list_action"
    description = "Generate or manage the shopping list."
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["generate", "list", "add", "remove", "clear"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "quantity": {"type": "number"},
                        "unit": {"type": "string"},
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
        if action == "list":
            items = list_items(deps.data_dir)
            return {"status": "ok", "items": items, "lines": format_list(items)}

        if action == "generate":
            plan = load_plan(deps.data_dir)
            if not plan:
                return {"status": "needs_clarification", "message": "No meal plan available.", "missing": ["plan"]}
            inventory = list_inventory(deps.data_dir)
            items = generate_from_plan(deps.data_dir, plan, inventory)
            return {"status": "ok", "items": items, "lines": format_list(items)}

        if action == "add":
            items = kwargs.get("items") or []
            if not items:
                return {"status": "needs_clarification", "missing": ["items"]}
            updated = add_items(deps.data_dir, items)
            return {"status": "ok", "items": updated, "lines": format_list(updated)}

        if action == "remove":
            items = kwargs.get("items") or []
            if not items:
                return {"status": "needs_clarification", "missing": ["items"]}
            updated = remove_items(deps.data_dir, items)
            return {"status": "ok", "items": updated, "lines": format_list(updated)}

        if action == "clear":
            clear_list(deps.data_dir)
            return {"status": "ok", "items": []}

        return {"status": "error", "message": "Unknown action."}
