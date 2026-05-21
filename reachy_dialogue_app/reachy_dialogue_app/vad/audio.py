from __future__ import annotations

import base64

import numpy as np

from .constants import SILERO_SAMPLE_RATE


def audio_rms_peak(samples: np.ndarray) -> tuple[float, float]:
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size == 0:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(np.square(values, dtype=np.float64))))
    peak = float(np.max(np.abs(values)))
    return rms, peak


def normalize_audio_sample(sample: np.ndarray, source_rate: int, target_rate: int = SILERO_SAMPLE_RATE) -> np.ndarray:
    values = np.asarray(sample, dtype=np.float32)
    if values.ndim == 2:
        values = values.mean(axis=1)
    values = np.clip(values.reshape(-1), -1.0, 1.0)
    if int(source_rate) == int(target_rate):
        return values.astype(np.float32, copy=False)
    return resample_linear(values, int(source_rate), int(target_rate))


def resample_linear(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size == 0 or source_rate == target_rate:
        return values.astype(np.float32, copy=False)
    duration = values.shape[0] / float(source_rate)
    target_length = max(1, int(round(duration * target_rate)))
    source_positions = np.linspace(0, values.shape[0] - 1, num=values.shape[0])
    target_positions = np.linspace(0, values.shape[0] - 1, num=target_length)
    return np.interp(target_positions, source_positions, values).astype(np.float32)


def float_to_pcm16_base64(samples: np.ndarray) -> str:

    values = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm = (values * 32767.0).astype("<i2")
    return base64.b64encode(pcm.tobytes()).decode("ascii")


def pcm16_bytes_to_float(audio_bytes: bytes) -> np.ndarray:
    if not audio_bytes:
        return np.zeros(0, dtype=np.float32)
    pcm = np.frombuffer(audio_bytes, dtype="<i2")
    return (pcm.astype(np.float32) / 32768.0).clip(-1.0, 1.0)
