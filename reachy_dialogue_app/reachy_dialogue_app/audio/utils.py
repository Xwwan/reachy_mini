from __future__ import annotations

import numpy as np


def _audio_level_from_rms(rms: float) -> float:
    return float(np.clip(rms / 0.08, 0.0, 1.0))
