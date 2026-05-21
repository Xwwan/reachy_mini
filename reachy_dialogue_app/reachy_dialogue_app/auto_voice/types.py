from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Literal

import numpy as np

from ..vad import VadConfig

AutoVoiceMode = Literal["local", "robot"]
RobotAudioSource = Callable[[], tuple[np.ndarray | None, int]]
StreamHook = Callable[[str, dict], tuple[list[tuple[str, dict]], threading.Event | None]]
StreamHookFactory = Callable[[str], StreamHook]


@dataclass
class AutoVoiceConfig:
    vad: VadConfig
    input_gain: float = 1.0
    local_chunk_queue_size: int = 80
    robot_poll_seconds: float = 0.01
    transcript_poll_seconds: float = 0.3
    service_timeout_seconds: int = 120


@dataclass
class AutoVoiceSnapshot:
    session_id: str
    mode: AutoVoiceMode
    state: str
    conversation_id: str
    tts_enabled: bool
    utterance_count: int
    last_error: str | None
    speech_probability: float
    rms: float
    peak: float


