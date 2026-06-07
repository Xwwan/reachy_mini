"""音频展示相关的小工具。"""

from __future__ import annotations

import numpy as np


def _audio_level_from_rms(rms: float) -> float:
    """把 RMS 映射成 0-1 的 UI 音量条数值。"""

    return float(np.clip(rms / 0.08, 0.0, 1.0))
