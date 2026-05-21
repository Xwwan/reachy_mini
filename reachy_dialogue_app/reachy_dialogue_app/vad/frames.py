from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class VadFrame:
    speech_probability: float
    is_speech: bool
    rms: float
    peak: float


@dataclass(frozen=True)
class VadEvent:
    event: str
    speech_probability: float
    rms: float
    peak: float
    duration_seconds: float = 0.0
    audio: np.ndarray | None = None
