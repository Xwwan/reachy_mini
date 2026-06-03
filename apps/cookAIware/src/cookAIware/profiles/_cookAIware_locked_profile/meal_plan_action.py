from __future__ import annotations

from typing import Any, Dict

from cookAIware.tools.core_tools import Tool, ToolDependencies
from cookAIware.family_profile import load_profile
from cookAIware.inventory import list_inventory
from cookAIware.meal_planner import generate_plan, load_plan, meal_for_day, resolve_day_to_date


class MealPlanActionTool(Tool):
    name = "meal_plan_action"
    description = "Generate a weekly meal plan, get the plan, query a day, or update a day."
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["generate", "get", "get_day", "update_day"]},
            "schedule_override": {"type": "object"},
            "day": {"type": "string"},
            "date": {"type": "string"},
            "meals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "meal": {"type": "string"},
                        "name": {"type": "string"},
                        "ingredients": {"type": "array", "items": {"type": "string"}},
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
        if action == "get":
            plan = load_plan(deps.data_dir)
            return {"status": "ok" if plan else "empty", "plan": plan}

        if action == "generate":
            profile = load_profile(deps.data_dir)
            adults = profile.get("adults")
            children = profile.get("children")
            if adults is None or children is None:
                return {
                    "status": "needs_clarification",
                    "message": "Family profile missing.",
                    "missing": ["adults", "children"],
                }
            inventory = list_inventory(deps.data_dir)
            if not inventory:
                return {
                    "status": "needs_clarification",
                    "message": "Inventory is empty.",
                    "missing": ["inventory"],
                }
            schedule_override = kwargs.get("schedule_override") or profile.get("schedule")
            plan = generate_plan(deps.data_dir, inventory, int(adults), int(children), schedule_override)
            return {"status": "ok", "plan": plan}

        if action == "get_day":
            plan = load_plan(deps.data_dir)
            if not plan:
                return {"status": "empty", "message": "No plan available."}
            day = kwargs.get("day")
            date_str = kwargs.get("date")
            if day and not date_str:
                date_str = resolve_day_to_date(day)
            entry = meal_for_day(plan, day, date_str)
            if not entry:
                return {"status": "empty", "message": "No meal found for that day."}
            return {"status": "ok", "day": entry}

        if action == "update_day":
            plan = load_plan(deps.data_dir)
            if not plan:
                return {"status": "empty", "message": "No plan available."}
            day = kwargs.get("day")
            date_str = kwargs.get("date")
            if day and not date_str:
                date_str = resolve_day_to_date(day)
            entry = meal_for_day(plan, day, date_str)
            if not entry:
                return {"status": "empty", "message": "No meal found for that day."}
            meals = kwargs.get("meals") or []
            if not meals:
                return {"status": "needs_clarification", "missing": ["meals"]}

            inventory = list_inventory(deps.data_dir)
            inventory_names = {item.get("name") for item in inventory if item.get("name")}
            display_map = {
                (item.get("display_name") or "").strip().lower(): item.get("name")
                for item in inventory
                if item.get("name")
            }
            for update in meals:
                meal_name = update.get("meal")
                if not meal_name:
                    continue
                target = next((m for m in entry.get("meals", []) if m.get("meal") == meal_name), None)
                if not target:
                    continue
                raw_ingredients = update.get("ingredients") or []
                ingredients = []
                for ing in raw_ingredients:
                    name = str(ing).strip().lower()
                    if not name:
                        continue
                    mapped = display_map.get(name, name)
                    if mapped not in inventory_names:
                        ingredients.append(name)
                    else:
                        ingredients.append(mapped)
                if ingredients:
                    from cookAIware.meal_planner import build_ingredient_entries

                    servings = int(target.get("servings") or 1)
                    target["ingredients"] = build_ingredient_entries(inventory, ingredients, servings)
                    if update.get("name"):
                        target["name"] = update.get("name")
                    else:
                        if len(ingredients) == 1:
                            target["name"] = ingredients[0]
                        elif len(ingredients) == 2:
                            target["name"] = f"{ingredients[0]} with {ingredients[1]}"
                        else:
                            target["name"] = f"{ingredients[0]} with {ingredients[1]} and {ingredients[2]}"
                elif update.get("name"):
                    target["name"] = update.get("name")

            from cookAIware.meal_planner import save_plan

            save_plan(deps.data_dir, plan)
            return {"status": "ok", "plan": plan}

        return {
            "status": "error",
            "message": "Unknown action. Use generate, get, get_day, or update_day.",
        }
