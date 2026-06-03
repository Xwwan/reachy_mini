from __future__ import annotations

from typing import Any, Dict

from cookAIware.tools.core_tools import Tool, ToolDependencies
from cookAIware.app_settings import get_settings, set_language


class AppSettingsActionTool(Tool):
    name = "app_settings_action"
    description = "Get or update app settings like language."
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "set_language"]},
            "language": {"type": "string"},
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        if deps.data_dir is None:
            return {"status": "error", "message": "Data directory not available."}

        action = kwargs.get("action")
        if action == "get":
            return {"status": "ok", "settings": get_settings(deps.data_dir)}

        if action == "set_language":
            language = (kwargs.get("language") or "").strip()
            if not language:
                return {"status": "needs_clarification", "missing": ["language"]}
            settings = set_language(deps.data_dir, language)
            if deps.handler is not None:
                try:
                    await deps.handler.apply_language(language)
                except Exception:
                    pass
            return {"status": "ok", "settings": settings}

        return {"status": "error", "message": "Unknown action."}
