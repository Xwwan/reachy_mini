"""行为与自动语音配置加载。

默认配置内置在代码中，用户可以通过 behavior_config.yaml/JSON 覆盖；同时保留
旧版 emoji_config.json 的兼容路径，方便历史配置继续工作。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..core.constants import (
    DEFAULT_BEHAVIOR_CONFIG_FILE,
    DEFAULT_EMOJI_CONFIG_FILE,
    DEFAULT_EMOJI_SERVICE_URL,
    DEFAULT_VAD_MODEL_FILE,
)

try:
    import yaml
except ImportError:  # pragma: no cover - dependency is declared in pyproject.
    yaml = None


def _default_behavior_config() -> dict[str, Any]:
    """返回完整默认配置，确保缺少配置文件时 app 仍可启动。"""

    return {
        "enabled": True,
        "auto_voice": {
            "model_path": str(DEFAULT_VAD_MODEL_FILE),
            "input_gain": 1.0,
            "local_chunk_queue_size": 80,
            "robot_poll_seconds": 0.01,
            "transcript_poll_seconds": 0.3,
            "service_timeout_seconds": 120,
            "playback_wait_grace_seconds": 0.1,
            "playback_wait_max_seconds": 0.0,
            "vad": {
                "speech_threshold": 0.5,
                "rms_speech_threshold": 0.01,
                "min_speech_ms": 250,
                "min_silence_ms": 900,
                "pre_roll_ms": 300,
                "post_roll_ms": 250,
                "max_utterance_ms": 15000,
                "cooldown_ms": 400,
            },
        },
        "modules": {
            "emoji": {
                "enabled": True,
                "tag_names": ["emo", "emotion", "表情"],
                "service_url": DEFAULT_EMOJI_SERVICE_URL,
                "request_timeout_seconds": 1.5,
                "method": "GET",
                "endpoint_template": "/{key}",
                "triggers": [
                    "😀",
                    "😄",
                    "😁",
                    "angry",
                    "sad",
                    "scared",
                    "fear",
                    "excited",
                    "idle",
                    "smug",
                    "surprised",
                    "surprise",
                    "😧",
                    "开心",
                    "难过",
                ],
            },
            "action": {
                "enabled": True,
                "tag_names": ["act", "action", "动作"],
                "trigger_mode": "function",
                "config_path": "../../action_call/config.json",
                "library_dir": "../../action_call/library",
                "sound": False,
                "final_home_check": True,
                "home_tolerance_deg": 5.0,
                "reset_duration": 1.5,
                "reset_attempts": 2,
                "triggers": "*",
            },
        },
    }


def _load_behavior_config() -> dict[str, Any]:
    """加载、合并并规范化行为配置。"""

    config = _default_behavior_config()
    config_path = _resolve_behavior_config_path()
    try:
        loaded = _load_structured_config(config_path)
        if isinstance(loaded, dict):
            _merge_behavior_config(config, loaded)
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"Failed to load behavior config {config_path}: {exc}")

    enabled_override = os.environ.get("REACHY_DIALOGUE_BEHAVIOR_ENABLED")
    if enabled_override is not None:
        config["enabled"] = _env_flag(enabled_override)
    else:
        config["enabled"] = bool(config.get("enabled", True))

    emoji_enabled = os.environ.get("REACHY_DIALOGUE_EMOJI_ENABLED")
    if emoji_enabled is not None:
        config["modules"]["emoji"]["enabled"] = _env_flag(emoji_enabled)

    emoji_url = (
        os.environ.get("REACHY_DIALOGUE_EMOJI_SERVICE_URL")
        or os.environ.get("REACHY_EMOJI_SERVICE_URL")
    )
    if emoji_url:
        config["modules"]["emoji"]["service_url"] = emoji_url

    _normalize_behavior_config(config, base_dir=config_path.parent)
    return config


def _resolve_behavior_config_path() -> Path:
    explicit = (
        os.environ.get("REACHY_DIALOGUE_BEHAVIOR_CONFIG")
        or os.environ.get("REACHY_DIALOGUE_EMOJI_CONFIG")
    )
    if explicit:
        return Path(explicit).expanduser()
    if DEFAULT_BEHAVIOR_CONFIG_FILE.exists():
        return DEFAULT_BEHAVIOR_CONFIG_FILE
    return DEFAULT_EMOJI_CONFIG_FILE


def _load_structured_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as config_file:
        if config_path.suffix.lower() in {".yaml", ".yml"}:
            if yaml is None:
                raise RuntimeError("PyYAML is required to load YAML config files")
            loaded = yaml.safe_load(config_file) or {}
        else:
            loaded = json.load(config_file)
    if isinstance(loaded, dict):
        return loaded
    return {}


def _merge_behavior_config(config: dict[str, Any], loaded: dict[str, Any]) -> None:
    """合并新版 modules 配置或旧版 emoji_config。"""

    if "modules" in loaded and isinstance(loaded.get("modules"), dict):
        if "enabled" in loaded:
            config["enabled"] = loaded["enabled"]
        for module_name, module_config in loaded["modules"].items():
            if not isinstance(module_config, dict):
                continue
            current = config["modules"].setdefault(str(module_name), {})
            current.update(module_config)
        for key, value in loaded.items():
            if key in {"enabled", "modules"}:
                continue
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                _deep_update(config[key], value)
            else:
                config[key] = value
        return

    # Legacy emoji_config.json support: use signal_map keys as emoji triggers.
    emoji_module = config["modules"]["emoji"]
    if "enabled" in loaded:
        config["enabled"] = loaded["enabled"]
        emoji_module["enabled"] = loaded["enabled"]
    for key in ("service_url", "request_timeout_seconds"):
        if key in loaded:
            emoji_module[key] = loaded[key]
    signal_map = loaded.get("signal_map")
    if isinstance(signal_map, dict):
        emoji_module["triggers"] = list(signal_map.keys())


def _deep_update(current: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            _deep_update(current[key], value)
        else:
            current[key] = value


def _normalize_behavior_config(config: dict[str, Any], *, base_dir: Path) -> None:
    """把模块配置统一成运行时代码更容易消费的形态。"""

    _normalize_auto_voice_config(config, base_dir=base_dir)
    modules = config.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    config["modules"] = modules
    for module_name, module_config in list(modules.items()):
        if not isinstance(module_config, dict):
            modules.pop(module_name)
            continue
        module_config["enabled"] = bool(module_config.get("enabled", True))
        module_config["tag_names"] = _normalize_string_list(
            module_config.get("tag_names")
        )
        if not module_config["tag_names"]:
            module_config["tag_names"] = [str(module_name)]
        module_config["service_url"] = str(
            module_config.get("service_url") or ""
        ).rstrip("/")
        module_config["method"] = str(module_config.get("method") or "GET").upper()
        module_config["trigger_mode"] = str(
            module_config.get("trigger_mode") or "http"
        ).lower()
        module_config["endpoint_template"] = str(
            module_config.get("endpoint_template") or "/{key}"
        )
        module_config["triggers"] = _normalize_triggers(
            module_config.get("triggers")
        )
        try:
            module_config["request_timeout_seconds"] = float(
                module_config.get("request_timeout_seconds", 3.0)
            )
        except (TypeError, ValueError):
            module_config["request_timeout_seconds"] = 3.0
        for key in ("config_path", "library_dir"):
            if key in module_config:
                module_config[key] = str(_resolve_behavior_path(module_config[key], base_dir))


def _normalize_auto_voice_config(config: dict[str, Any], *, base_dir: Path) -> None:
    """规范化 auto_voice 子配置，包括模型路径和数值参数。"""

    auto_voice = config.get("auto_voice")
    if not isinstance(auto_voice, dict):
        auto_voice = {}
    default = _default_behavior_config()["auto_voice"]
    merged = dict(default)
    _deep_update(merged, auto_voice)
    auto_voice = merged
    if auto_voice.get("model_path"):
        auto_voice["model_path"] = str(
            _resolve_behavior_path(auto_voice["model_path"], base_dir)
        )
    auto_voice["local_chunk_queue_size"] = _coerce_int(
        auto_voice.get("local_chunk_queue_size"), 80
    )
    auto_voice["input_gain"] = _coerce_float(auto_voice.get("input_gain"), 1.0)
    auto_voice["robot_poll_seconds"] = _coerce_float(
        auto_voice.get("robot_poll_seconds"), 0.01
    )
    auto_voice["transcript_poll_seconds"] = _coerce_float(
        auto_voice.get("transcript_poll_seconds"), 0.3
    )
    auto_voice["service_timeout_seconds"] = _coerce_int(
        auto_voice.get("service_timeout_seconds"), 120
    )
    auto_voice["playback_wait_grace_seconds"] = _coerce_float(
        auto_voice.get("playback_wait_grace_seconds"), 0.1
    )
    auto_voice["playback_wait_max_seconds"] = _coerce_float(
        auto_voice.get("playback_wait_max_seconds"), 0.0
    )
    vad = auto_voice.get("vad")
    if not isinstance(vad, dict):
        vad = {}
    vad_defaults = default["vad"]
    auto_voice["vad"] = {
        "speech_threshold": _coerce_float(
            vad.get("speech_threshold"), vad_defaults["speech_threshold"]
        ),
        "rms_speech_threshold": _coerce_float(
            vad.get("rms_speech_threshold"),
            vad_defaults["rms_speech_threshold"],
        ),
        "min_speech_ms": _coerce_int(
            vad.get("min_speech_ms"), vad_defaults["min_speech_ms"]
        ),
        "min_silence_ms": _coerce_int(
            vad.get("min_silence_ms"), vad_defaults["min_silence_ms"]
        ),
        "pre_roll_ms": _coerce_int(
            vad.get("pre_roll_ms"), vad_defaults["pre_roll_ms"]
        ),
        "post_roll_ms": _coerce_int(
            vad.get("post_roll_ms"), vad_defaults["post_roll_ms"]
        ),
        "max_utterance_ms": _coerce_int(
            vad.get("max_utterance_ms"), vad_defaults["max_utterance_ms"]
        ),
        "cooldown_ms": _coerce_int(
            vad.get("cooldown_ms"), vad_defaults["cooldown_ms"]
        ),
    }
    config["auto_voice"] = auto_voice


def _resolve_behavior_path(value: Any, base_dir: Path) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_triggers(value: Any) -> str | list[str]:
    if value == "*":
        return "*"
    return _normalize_string_list(value)


def _env_flag(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def _public_behavior_config(config: dict[str, Any]) -> dict[str, Any]:
    public_modules: dict[str, Any] = {}
    for module_name, module_config in (config.get("modules") or {}).items():
        if not isinstance(module_config, dict):
            continue
        public_modules[str(module_name)] = {
            "enabled": bool(module_config.get("enabled")),
            "tag_names": list(module_config.get("tag_names") or []),
            "trigger_mode": module_config.get("trigger_mode"),
            "service_url": module_config.get("service_url"),
            "method": module_config.get("method"),
            "endpoint_template": module_config.get("endpoint_template"),
            "config_path": module_config.get("config_path"),
            "library_dir": module_config.get("library_dir"),
            "triggers": module_config.get("triggers"),
        }
    return {
        "enabled": bool(config.get("enabled")),
        "auto_voice": config.get("auto_voice"),
        "modules": public_modules,
    }


def _public_emoji_config(config: dict[str, Any]) -> dict[str, Any]:
    emoji_module = (config.get("modules") or {}).get("emoji") or {}
    triggers = emoji_module.get("triggers")
    signal_map = {}
    if isinstance(triggers, list):
        signal_map = {trigger: trigger for trigger in triggers}
    return {
        "enabled": bool(config.get("enabled") and emoji_module.get("enabled", True)),
        "service_url": emoji_module.get("service_url"),
        "signal_map": signal_map,
        "available_emotions": [],
        "tag_names": list(emoji_module.get("tag_names") or []),
        "triggers": triggers,
    }
