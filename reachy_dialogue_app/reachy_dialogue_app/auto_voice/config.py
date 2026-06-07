"""自动语音配置读取与规范化。

配置来源按优先级合并：环境变量 > behavior_config.yaml > 代码默认值。
这样既方便部署时临时调参，也能让默认配置在没有外部文件时直接可用。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.constants import DEFAULT_VAD_MODEL_FILE
from ..vad import VadConfig
from .types import AutoVoiceConfig, WakeGateConfig


def _auto_voice_model_path(behavior_config: dict[str, Any] | None = None) -> Path:
    """解析 Silero VAD 模型路径。"""

    auto_voice = _auto_voice_section(behavior_config)
    configured = auto_voice.get("model_path")
    return Path(
        os.environ.get("REACHY_DIALOGUE_VAD_MODEL")
        or configured
        or DEFAULT_VAD_MODEL_FILE
    ).expanduser()


def _auto_voice_config(behavior_config: dict[str, Any] | None = None) -> AutoVoiceConfig:
    """从行为配置和环境变量构造 AutoVoiceConfig。"""

    auto_voice = _auto_voice_section(behavior_config)
    vad_config = auto_voice.get("vad")
    if not isinstance(vad_config, dict):
        vad_config = {}
    vad = VadConfig(
        speech_threshold=_float_setting(
            vad_config, "speech_threshold", "REACHY_DIALOGUE_VAD_THRESHOLD", 0.5
        ),
        rms_speech_threshold=_float_setting(
            vad_config,
            "rms_speech_threshold",
            "REACHY_DIALOGUE_VAD_RMS_SPEECH_THRESHOLD",
            0.01,
        ),
        min_speech_ms=_int_setting(
            vad_config, "min_speech_ms", "REACHY_DIALOGUE_VAD_MIN_SPEECH_MS", 250
        ),
        min_silence_ms=_int_setting(
            vad_config, "min_silence_ms", "REACHY_DIALOGUE_VAD_MIN_SILENCE_MS", 900
        ),
        pre_roll_ms=_int_setting(
            vad_config, "pre_roll_ms", "REACHY_DIALOGUE_VAD_PRE_ROLL_MS", 300
        ),
        post_roll_ms=_int_setting(
            vad_config, "post_roll_ms", "REACHY_DIALOGUE_VAD_POST_ROLL_MS", 250
        ),
        max_utterance_ms=_int_setting(
            vad_config,
            "max_utterance_ms",
            "REACHY_DIALOGUE_VAD_MAX_UTTERANCE_MS",
            15000,
        ),
        cooldown_ms=_int_setting(
            vad_config, "cooldown_ms", "REACHY_DIALOGUE_VAD_COOLDOWN_MS", 400
        ),
    )
    return AutoVoiceConfig(
        vad=vad,
        input_gain=_float_setting(
            auto_voice,
            "input_gain",
            "REACHY_DIALOGUE_AUTO_VOICE_INPUT_GAIN",
            1.0,
        ),
        local_chunk_queue_size=_int_setting(
            auto_voice,
            "local_chunk_queue_size",
            "REACHY_DIALOGUE_AUTO_VOICE_QUEUE_SIZE",
            80,
        ),
        robot_poll_seconds=_float_setting(
            auto_voice,
            "robot_poll_seconds",
            "REACHY_DIALOGUE_AUTO_VOICE_ROBOT_POLL_SECONDS",
            0.01,
        ),
        transcript_poll_seconds=_float_setting(
            auto_voice,
            "transcript_poll_seconds",
            "REACHY_DIALOGUE_AUTO_VOICE_TRANSCRIPT_POLL_SECONDS",
            0.3,
        ),
        service_timeout_seconds=_int_setting(
            auto_voice,
            "service_timeout_seconds",
            "REACHY_DIALOGUE_AUTO_VOICE_SERVICE_TIMEOUT_SECONDS",
            120,
        ),
        playback_wait_grace_seconds=_float_setting(
            auto_voice,
            "playback_wait_grace_seconds",
            "REACHY_DIALOGUE_AUTO_VOICE_PLAYBACK_WAIT_GRACE_SECONDS",
            0.1,
        ),
        playback_wait_max_seconds=_float_setting(
            auto_voice,
            "playback_wait_max_seconds",
            "REACHY_DIALOGUE_AUTO_VOICE_PLAYBACK_WAIT_MAX_SECONDS",
            0.0,
        ),
        wake_gate=_wake_gate_config(auto_voice),
    )


def _auto_voice_section(behavior_config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(behavior_config, dict):
        return {}
    auto_voice = behavior_config.get("auto_voice")
    return auto_voice if isinstance(auto_voice, dict) else {}


def _wake_gate_config(auto_voice: dict[str, Any]) -> WakeGateConfig:
    """读取唤醒词门控配置；缺省时保持关闭，避免影响普通连续对话。"""

    wake_gate = auto_voice.get("wake_gate")
    if not isinstance(wake_gate, dict):
        return WakeGateConfig()
    return WakeGateConfig(
        enabled=_bool_setting(wake_gate, "enabled", False),
        wake_phrases=_string_tuple_setting(wake_gate, "wake_phrases"),
        exit_phrases=_string_tuple_setting(wake_gate, "exit_phrases"),
        idle_timeout_seconds=_float_setting(
            wake_gate,
            "idle_timeout_seconds",
            "REACHY_DIALOGUE_WAKE_IDLE_TIMEOUT_SECONDS",
            60.0,
        ),
        wake_reply=_string_setting(wake_gate, "wake_reply", "我在。"),
        sleep_reply=_string_setting(wake_gate, "sleep_reply", "好，我先休息。"),
    )


def _int_setting(
    config: dict[str, Any],
    key: str,
    env_key: str,
    default: int,
) -> int:
    """读取整数配置，非法值自动回退默认值。"""

    value = os.environ.get(env_key, config.get(key, default))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_setting(
    config: dict[str, Any],
    key: str,
    env_key: str,
    default: float,
) -> float:
    """读取浮点配置，非法值自动回退默认值。"""

    value = os.environ.get(env_key, config.get(key, default))
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool_setting(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _string_setting(config: dict[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _string_tuple_setting(config: dict[str, Any], key: str) -> tuple[str, ...]:
    value = config.get(key)
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        items = []
    return tuple(str(item).strip() for item in items if str(item).strip())
