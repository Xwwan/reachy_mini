from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cookAIware.data_store import load_json, save_json
from cookAIware.family_profile import default_schedule
from cookAIware.inventory import unit_for_item
from cookAIware.units import format_quantity


MEAL_PLAN_FILE = "meal_plan.json"


DAY_NAMES = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


PROTEIN_KEYS = {
    "chicken",
    "turkey",
    "beef",
    "pork",
    "fish",
    "salmon",
    "tuna",
    "tofu",
}
LEGUME_KEYS = {"beans", "lentils", "chickpeas"}
VEG_KEYS = {
    "broccoli",
    "carrot",
    "spinach",
    "lettuce",
    "pepper",
    "tomato",
    "zucchini",
    "onion",
    "salad",
    "mixed vegetables",
    "vegetable mix",
}
GRAIN_KEYS = {"rice", "pasta", "bread", "oats", "quinoa", "couscous", "wrap", "flatbread"}
DAIRY_KEYS = {"milk", "yogurt", "cheese"}
FRUIT_KEYS = {
    "apple",
    "banana",
    "orange",
    "berries",
    "grape",
    "pear",
}


def _plan_path(data_dir: Path) -> Path:
    return data_dir / MEAL_PLAN_FILE


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _display_name(name: str) -> str:
    return " ".join(part.capitalize() for part in _normalize_name(name).split())


def categorize(name: str) -> str:
    n = _normalize_name(name)
    for key in PROTEIN_KEYS:
        if key in n:
            return "protein"
    for key in LEGUME_KEYS:
        if key in n:
            return "legume"
    for key in VEG_KEYS:
        if key in n:
            return "veg"
    for key in GRAIN_KEYS:
        if key in n:
            return "grain"
    for key in DAIRY_KEYS:
        if key in n:
            return "dairy"
    for key in FRUIT_KEYS:
        if key in n:
            return "fruit"
    return "other"


def week_start_for(today: date | None = None) -> date:
    today = today or date.today()
    return today - timedelta(days=today.weekday())


def load_plan(data_dir: Path) -> Dict[str, Any]:
    return load_json(_plan_path(data_dir), {})


def save_plan(data_dir: Path, plan: Dict[str, Any]) -> None:
    save_json(_plan_path(data_dir), plan)


def _default_meal_slots(schedule: Dict[str, Any]) -> List[Dict[str, Any]]:
    slots: List[Dict[str, Any]] = []
    weekday = schedule.get("weekday", {})
    weekend = schedule.get("weekend", {})
    lunch_days = set((weekday.get("lunch_days") or []))

    for idx, day in enumerate(DAY_NAMES):
        is_weekend = idx >= 5
        meals: List[Dict[str, Any]] = []
        if is_weekend:
            if weekend.get("breakfast"):
                meals.append({"meal": "breakfast", "servings": None})
            if weekend.get("lunch"):
                meals.append({"meal": "lunch", "servings": None})
            if weekend.get("dinner"):
                meals.append({"meal": "dinner", "servings": None})
        else:
            if weekday.get("dinner"):
                meals.append({"meal": "dinner", "servings": None})
            if day in lunch_days:
                meals.append({"meal": "adult_lunch", "servings": 1})

        slots.append({"day": day, "meals": meals})
    return slots


def _select_ingredients(inventory: List[Dict[str, Any]], meal_type: str, start_idx: int) -> Tuple[List[str], int]:
    names = [str(item.get("name")) for item in inventory if item.get("name")]
    if not names:
        return [], start_idx

    proteins = [n for n in names if categorize(n) == "protein"]
    legumes = [n for n in names if categorize(n) == "legume"]
    vegs = [n for n in names if categorize(n) == "veg"]
    grains = [n for n in names if categorize(n) == "grain"]
    dairies = [n for n in names if categorize(n) == "dairy"]
    fruits = [n for n in names if categorize(n) == "fruit"]
    others = [n for n in names if categorize(n) == "other"]

    ingredients: List[str] = []

    def pick(pool: List[str]) -> str | None:
        nonlocal start_idx
        if not pool:
            return None
        choice = pool[start_idx % len(pool)]
        start_idx += 1
        return choice

    def pick_from_category(cat: str) -> str | None:
        pools = {
            "protein": proteins,
            "legume": legumes,
            "veg": vegs,
            "grain": grains,
            "dairy": dairies,
            "fruit": fruits,
            "other": others,
        }
        return pick(pools.get(cat, []))

    if meal_type in ("breakfast",):
        templates = [
            ["dairy", "fruit", "grain"],
            ["fruit", "grain"],
            ["dairy", "grain"],
        ]
    else:
        templates = [
            ["protein", "veg", "grain"],
            ["legume", "veg", "grain"],
            ["protein", "veg"],
            ["legume", "veg"],
            ["grain", "veg"],
        ]

    for template in templates:
        candidate: List[str] = []
        for cat in template:
            item = pick_from_category(cat)
            if item and item not in candidate:
                candidate.append(item)
        if candidate:
            ingredients = candidate
            break

    if not ingredients and names:
        ingredients = [names[start_idx % len(names)]]
        start_idx += 1

    return ingredients, start_idx


def _estimate_per_serving(unit: str, category: str) -> float:
    if unit == "g":
        if category == "protein":
            return 150.0
        if category == "legume":
            return 140.0
        if category == "veg":
            return 120.0
        if category == "grain":
            return 100.0
        return 80.0
    if unit == "ml":
        return 200.0
    if unit == "pcs":
        return 1.0
    return 1.0


def build_ingredient_entries(
    inventory: List[Dict[str, Any]],
    ingredient_names: List[str],
    servings: int,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for ing in ingredient_names:
        unit = unit_for_item(inventory, ing) or "pcs"
        per_serving = _estimate_per_serving(unit, categorize(ing))
        required = per_serving * servings
        entries.append(
            {
                "name": ing,
                "display_name": _display_name(ing),
                "quantity": required,
                "unit": unit,
                "display_quantity": format_quantity(required, unit),
            }
        )
    return entries


def generate_plan(
    data_dir: Path,
    inventory: List[Dict[str, Any]],
    adults: int,
    children: int,
    schedule_override: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    schedule = schedule_override or default_schedule()
    week_start = week_start_for().isoformat()
    meal_slots = _default_meal_slots(schedule)

    servings_all = adults + children
    index = 0
    plan_days: List[Dict[str, Any]] = []
    last_signature: str | None = None

    for day_idx, day_slot in enumerate(meal_slots):
        day_name = day_slot["day"]
        day_date = (week_start_for() + timedelta(days=day_idx)).isoformat()
        meals_out: List[Dict[str, Any]] = []
        for meal in day_slot["meals"]:
            meal_type = meal["meal"]
            servings = meal.get("servings") or servings_all
            ingredients = []
            for _ in range(3):
                candidate, index = _select_ingredients(inventory, meal_type, index)
                signature = ",".join(sorted(candidate))
                if signature and signature == last_signature:
                    index += 1
                    continue
                ingredients = candidate
                last_signature = signature
                break
            ingredient_entries: List[Dict[str, Any]] = []
            for ing in ingredients:
                unit = unit_for_item(inventory, ing) or "pcs"
                per_serving = _estimate_per_serving(unit, categorize(ing))
                required = per_serving * servings
                ingredient_entries.append(
                    {
                        "name": ing,
                        "display_name": _display_name(ing),
                        "quantity": required,
                        "unit": unit,
                        "display_quantity": format_quantity(required, unit),
                    }
                )
            if ingredients:
                if len(ingredients) == 1:
                    meal_name = _display_name(ingredients[0])
                elif len(ingredients) == 2:
                    meal_name = f"{_display_name(ingredients[0])} with {_display_name(ingredients[1])}"
                else:
                    meal_name = f"{_display_name(ingredients[0])} with {_display_name(ingredients[1])} and {_display_name(ingredients[2])}"
            else:
                meal_name = "No available ingredients"
            meals_out.append(
                {
                    "meal": meal_type,
                    "name": meal_name,
                    "servings": servings,
                    "ingredients": ingredient_entries,
                }
            )
        plan_days.append({"day": day_name, "date": day_date, "meals": meals_out})

    plan = {
        "week_start": week_start,
        "schedule": schedule,
        "days": plan_days,
    }
    save_plan(data_dir, plan)
    return plan


def meal_for_day(plan: Dict[str, Any], day: str | None, date_str: str | None) -> Dict[str, Any] | None:
    if not plan:
        return None
    if date_str:
        for entry in plan.get("days", []):
            if entry.get("date") == date_str:
                return entry
    if day:
        day_norm = day.strip().lower()
        for entry in plan.get("days", []):
            if entry.get("day") == day_norm:
                return entry
    return None


def resolve_day_to_date(day: str) -> str | None:
    try:
        day_norm = day.strip().lower()
    except Exception:
        return None
    if day_norm not in DAY_NAMES:
        return None
    start = week_start_for()
    idx = DAY_NAMES.index(day_norm)
    return (start + timedelta(days=idx)).isoformat()
