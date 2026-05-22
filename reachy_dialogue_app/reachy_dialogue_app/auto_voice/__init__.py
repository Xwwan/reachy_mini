from .config import _auto_voice_config, _auto_voice_model_path
from .hooks import _auto_voice_stream_hook_factory
from .manager import AutoVoiceManager
from .session import AutoVoiceSession
from .sse import audio_duration_from_payload, decode_sse_json, iter_sse_events, json_or_error
from .types import (
    AutoVoiceConfig,
    AutoVoiceGateState,
    AutoVoiceMode,
    AutoVoiceSnapshot,
    RobotAudioSource,
    StreamHook,
    StreamHookFactory,
    WakeGateConfig,
)

__all__ = [
    "AutoVoiceConfig",
    "AutoVoiceGateState",
    "AutoVoiceManager",
    "AutoVoiceMode",
    "AutoVoiceSession",
    "AutoVoiceSnapshot",
    "RobotAudioSource",
    "StreamHook",
    "StreamHookFactory",
    "WakeGateConfig",
    "_auto_voice_config",
    "_auto_voice_model_path",
    "_auto_voice_stream_hook_factory",
    "audio_duration_from_payload",
    "decode_sse_json",
    "iter_sse_events",
    "json_or_error",
]
