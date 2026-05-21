from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


SILERO_SAMPLE_RATE = 16000
SILERO_CHUNK_SIZE = 512


@dataclass(frozen=True)
class VadConfig:
    sample_rate: int = SILERO_SAMPLE_RATE
    speech_threshold: float = 0.5
    rms_speech_threshold: float = 0.01
    min_speech_ms: int = 250
    min_silence_ms: int = 900
    pre_roll_ms: int = 300
    post_roll_ms: int = 250
    max_utterance_ms: int = 15000
    cooldown_ms: int = 400


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


class SileroVad:
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


class UtteranceSegmenter:
    def __init__(self, vad: SileroVad, config: VadConfig):
        self.vad = vad
        self.config = config
        self.reset()

    def reset(self) -> None:
        self.vad.reset()
        self.pending = np.zeros(0, dtype=np.float32)
        self.pre_roll: list[np.ndarray] = []
        self.current: list[np.ndarray] = []
        self.speech_samples = 0
        self.silence_samples = 0
        self.in_speech = False
        self.started_at = 0.0
        self.last_probability = 0.0
        self.last_rms = 0.0
        self.last_peak = 0.0

    def feed(self, samples: np.ndarray) -> list[VadEvent]:
        incoming = np.asarray(samples, dtype=np.float32).reshape(-1)
        if incoming.size == 0:
            return []
        self.pending = np.concatenate([self.pending, incoming])
        events: list[VadEvent] = []
        while self.pending.shape[0] >= SILERO_CHUNK_SIZE:
            chunk = self.pending[:SILERO_CHUNK_SIZE]
            self.pending = self.pending[SILERO_CHUNK_SIZE:]
            event = self._feed_chunk(chunk)
            if event is not None:
                events.append(event)
        return events

    def _feed_chunk(self, chunk: np.ndarray) -> VadEvent | None:
        frame = self.vad.predict_chunk(chunk)
        is_speech = (
            frame.speech_probability >= self.config.speech_threshold
            or (
                self.config.rms_speech_threshold > 0
                and frame.rms >= self.config.rms_speech_threshold
            )
        )
        self.last_probability = frame.speech_probability
        self.last_rms = frame.rms
        self.last_peak = frame.peak

        if is_speech:
            if not self.in_speech:
                self.in_speech = True
                self.started_at = time.monotonic()
                self.current = [*self.pre_roll, chunk.copy()]
                self.speech_samples = chunk.shape[0]
                self.silence_samples = 0
                return VadEvent(
                    event="speech_start",
                    speech_probability=frame.speech_probability,
                    rms=frame.rms,
                    peak=frame.peak,
                    audio=np.concatenate(self.current),
                )
            self.current.append(chunk.copy())
            self.speech_samples += chunk.shape[0]
            self.silence_samples = 0
            if self._samples_to_ms(sum(part.shape[0] for part in self.current)) >= self.config.max_utterance_ms:
                return self._finish(frame, forced=True)
            return None

        if self.in_speech:
            self.current.append(chunk.copy())
            self.silence_samples += chunk.shape[0]
            if self._samples_to_ms(self.silence_samples) >= (
                self.config.min_silence_ms + self.config.post_roll_ms
            ):
                return self._finish(frame, forced=False)
            return None

        self.pre_roll.append(chunk.copy())
        max_pre_roll = max(1, int(self.config.pre_roll_ms / self._chunk_ms()))
        if len(self.pre_roll) > max_pre_roll:
            self.pre_roll = self.pre_roll[-max_pre_roll:]
        return None

    def _finish(self, frame: VadFrame, *, forced: bool) -> VadEvent:
        audio = np.concatenate(self.current) if self.current else np.zeros(0, dtype=np.float32)
        duration = audio.shape[0] / float(self.config.sample_rate)
        min_speech_samples = int(self.config.min_speech_ms * self.config.sample_rate / 1000)
        self.in_speech = False
        self.current = []
        self.speech_samples = 0
        self.silence_samples = 0
        self.pre_roll = []
        if audio.shape[0] < min_speech_samples:
            return VadEvent(
                event="speech_cancelled",
                speech_probability=frame.speech_probability,
                rms=frame.rms,
                peak=frame.peak,
                duration_seconds=duration,
                audio=None,
            )
        return VadEvent(
            event="speech_end_forced" if forced else "speech_end",
            speech_probability=frame.speech_probability,
            rms=frame.rms,
            peak=frame.peak,
            duration_seconds=duration,
            audio=audio,
        )

    def _chunk_ms(self) -> float:
        return SILERO_CHUNK_SIZE * 1000.0 / float(self.config.sample_rate)

    def _samples_to_ms(self, samples: int) -> float:
        return samples * 1000.0 / float(self.config.sample_rate)


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
    import base64

    values = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm = (values * 32767.0).astype("<i2")
    return base64.b64encode(pcm.tobytes()).decode("ascii")


def pcm16_bytes_to_float(audio_bytes: bytes) -> np.ndarray:
    if not audio_bytes:
        return np.zeros(0, dtype=np.float32)
    pcm = np.frombuffer(audio_bytes, dtype="<i2")
    return (pcm.astype(np.float32) / 32768.0).clip(-1.0, 1.0)
