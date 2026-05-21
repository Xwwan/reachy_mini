from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.constants import DEFAULT_VAD_MODEL_FILE
from ..vad import VadConfig
from .types import AutoVoiceConfig


def _auto_voice_model_path(behavior_config: dict[str, Any] | None = None) -> Path:
    auto_voice = _auto_voice_section(behavior_config)
    configured = auto_voice.get("model_path")
    return Path(
        os.environ.get("REACHY_DIALOGUE_VAD_MODEL")
        or configured
        or DEFAULT_VAD_MODEL_FILE
    ).expanduser()


def _auto_voice_config(behavior_config: dict[str, Any] | None = None) -> AutoVoiceConfig:
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
    )


def _auto_voice_section(behavior_config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(behavior_config, dict):
        return {}
    auto_voice = behavior_config.get("auto_voice")
    return auto_voice if isinstance(auto_voice, dict) else {}


def _int_setting(
    config: dict[str, Any],
    key: str,
    env_key: str,
    default: int,
) -> int:
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
    value = os.environ.get(env_key, config.get(key, default))
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
