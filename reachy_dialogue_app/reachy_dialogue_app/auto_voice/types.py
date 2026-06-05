from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Literal

import numpy as np

from ..vad import VadConfig

AutoVoiceMode = Literal["local", "robot"]
AutoVoiceGateState = Literal["awake", "waiting_wake"]
RobotAudioSource = Callable[[], tuple[np.ndarray | None, int]]
StreamHook = Callable[[str, dict], tuple[list[tuple[str, dict]], threading.Event | None]]
StreamHookFactory = Callable[[str], StreamHook]


@dataclass(frozen=True)
class WakeGateConfig:
    enabled: bool = False
    wake_phrases: tuple[str, ...] = ()
    exit_phrases: tuple[str, ...] = ()
    idle_timeout_seconds: float = 60.0
    wake_reply: str = "我在。"
    sleep_reply: str = "好，我先休息。"


@dataclass
class AutoVoiceConfig:
    vad: VadConfig
    input_gain: float = 1.0
    local_chunk_queue_size: int = 80
    robot_poll_seconds: float = 0.01
    transcript_poll_seconds: float = 0.3
    service_timeout_seconds: int = 120
    playback_wait_grace_seconds: float = 0.1
    playback_wait_max_seconds: float = 0.0
    wake_gate: WakeGateConfig = field(default_factory=WakeGateConfig)


@dataclass
class AutoVoiceSnapshot:
    session_id: str
    mode: AutoVoiceMode
    state: str
    conversation_id: str
    tts_enabled: bool
    utterance_count: int
    gate_state: AutoVoiceGateState
    wake_gate_enabled: bool
    last_error: str | None
    speech_probability: float
    rms: float
    peak: float
