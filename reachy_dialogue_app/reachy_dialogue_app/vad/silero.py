"""Silero ONNX VAD 推理封装。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .audio import audio_rms_peak
from .constants import SILERO_CHUNK_SIZE, SILERO_SAMPLE_RATE
from .frames import VadFrame


class SileroVad:
    """维护 Silero VAD 的 ONNX Runtime session 和递归状态。"""

    """Small wrapper for common Silero VAD ONNX exports."""

    def __init__(self, model_path: Path, *, sample_rate: int = SILERO_SAMPLE_RATE):
        self.model_path = Path(model_path)
        self.sample_rate = int(sample_rate)
        if self.sample_rate != SILERO_SAMPLE_RATE:
            raise ValueError("Silero VAD wrapper currently expects 16 kHz input.")
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Silero VAD model not found: {self.model_path}. "
                "Run reachy_dialogue_app/scripts/download_silero_vad.py"
            )
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "onnxruntime is required for automatic voice mode. "
                "Install the reachy_dialogue_app dependencies first."
            ) from exc

        self.session = ort.InferenceSession(
            str(self.model_path),
            providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.output_names = [item.name for item in self.session.get_outputs()]
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)

    def predict_chunk(self, chunk: np.ndarray) -> VadFrame:
        """对一个固定大小 chunk 预测语音概率，并返回音量指标。"""

        samples = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if samples.shape[0] != SILERO_CHUNK_SIZE:
            raise ValueError(f"Silero VAD expects {SILERO_CHUNK_SIZE} samples.")

        inputs: dict[str, Any] = {}
        if "input" in self.input_names:
            inputs["input"] = samples.reshape(1, -1)
        else:
            first_name = next(iter(self.input_names))
            inputs[first_name] = samples.reshape(1, -1)
        if "state" in self.input_names:
            inputs["state"] = self.state
        if "sr" in self.input_names:
            inputs["sr"] = np.array(self.sample_rate, dtype=np.int64)

        outputs = self.session.run(None, inputs)
        probability = float(np.asarray(outputs[0]).reshape(-1)[0])
        if len(outputs) > 1:
            next_state = np.asarray(outputs[1], dtype=np.float32)
            if next_state.shape == self.state.shape:
                self.state = next_state
        rms, peak = audio_rms_peak(samples)
        return VadFrame(
            speech_probability=probability,
            is_speech=probability >= 0.5,
            rms=rms,
            peak=peak,
        )
