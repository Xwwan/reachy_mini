from __future__ import annotations

from typing import Any, Dict

from cookAIware.tools.core_tools import Tool, ToolDependencies
from cookAIware.family_profile import load_profile, update_profile, default_schedule


class FamilyProfileActionTool(Tool):
    name = "family_profile_action"
    description = "Get or update the family profile (adults, children, schedule)."
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "set"]},
            "adults": {"type": "integer"},
            "children": {"type": "integer"},
            "schedule": {"type": "object"},
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        if deps.data_dir is None:
            return {"status": "error", "message": "Data directory not available."}

        action = kwargs.get("action")
        if action == "get":
            profile = load_profile(deps.data_dir)
            missing = []
            if profile.get("adults") in (None, 0):
                missing.append("adults")
            if profile.get("children") in (None,):
                missing.append("children")
            if not profile.get("schedule"):
                profile["schedule"] = default_schedule()
            return {
                "status": "needs_clarification" if missing else "ok",
                "profile": profile,
                "missing": missing,
            }

        if action == "set":
            adults = kwargs.get("adults")
            children = kwargs.get("children")
            schedule = kwargs.get("schedule")
            profile = update_profile(deps.data_dir, adults, children, schedule)
            return {"status": "ok", "profile": profile}

        return {"status": "error", "message": "Unknown action."}
