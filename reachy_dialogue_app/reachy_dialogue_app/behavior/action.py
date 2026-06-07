"""本地动作库触发入口。

LLM 回复中的 action 标签最终会落到这里，由 action_call 模块读取动作库并驱动
Reachy Mini。导入 action_call 可能失败，所以保持延迟导入，只有真正触发动作
时才加载。
"""

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
    """按动作配置播放一个动作信号。"""

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
    """延迟导入仓库根目录下的 action_call 包。"""

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

