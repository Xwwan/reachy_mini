"""VAD 分段配置。"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import SILERO_SAMPLE_RATE


@dataclass(frozen=True)
class VadConfig:
    """Silero VAD 与后处理分段参数。"""

    sample_rate: int = SILERO_SAMPLE_RATE
    speech_threshold: float = 0.5
    rms_speech_threshold: float = 0.01
    min_speech_ms: int = 250
    min_silence_ms: int = 900
    pre_roll_ms: int = 300
    post_roll_ms: int = 250
    max_utterance_ms: int = 15000
    cooldown_ms: int = 400
