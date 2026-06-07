"""VAD 单帧和分段事件的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class VadFrame:
    """Silero 对固定长度音频块的预测结果。"""

    speech_probability: float
    is_speech: bool
    rms: float
    peak: float


@dataclass(frozen=True)
class VadEvent:
    """UtteranceSegmenter 输出给自动语音状态机的语音事件。"""

    event: str
    speech_probability: float
    rms: float
    peak: float
    duration_seconds: float = 0.0
    audio: np.ndarray | None = None
