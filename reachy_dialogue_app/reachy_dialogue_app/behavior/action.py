from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from ..core.constants import REPO_ROOT


def _play_action_signal(
    reachy_mini: ReachyMini,
    signal: str,
    action_config: dict[str, Any] | None,
) -> None:
    module = _load_action_call_module()
    config = action_config or {}
    module.play_signal_on_reachy(
        reachy_mini,
        signal,
        config_path=Path(
            config.get("config_path") or REPO_ROOT / "action_call" / "config.json"
        ),
        library_dir=Path(
            config.get("library_dir") or REPO_ROOT / "action_call" / "library"
        ),
        sound=bool(config.get("sound", False)),
        final_home_check=bool(config.get("final_home_check", True)),
        home_tolerance_deg=float(config.get("home_tolerance_deg", 5.0)),
        reset_duration=float(config.get("reset_duration", 1.5)),
        reset_attempts=int(config.get("reset_attempts", 2)),
    )


def _load_action_call_module() -> Any:
    module_path = REPO_ROOT / "action_call" / "play_emotion_action.py"
    spec = importlib.util.spec_from_file_location(
        "reachy_dialogue_action_call",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load action_call module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


