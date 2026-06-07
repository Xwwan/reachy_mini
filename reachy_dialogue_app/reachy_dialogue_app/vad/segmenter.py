"""基于 VAD 帧的用户语音分段器。

Silero 只给出短 chunk 的语音概率；Segmenter 在其上叠加 pre-roll、最短语音、
最短静音、最大句长等策略，输出 speech_start/end/cancelled 事件。
"""

from __future__ import annotations

import time

import numpy as np

from .constants import SILERO_CHUNK_SIZE
from .config import VadConfig
from .frames import VadEvent, VadFrame
from .silero import SileroVad


class UtteranceSegmenter:
    """把连续音频流切成一段段可发送给对话服务的用户话语。"""

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
        """喂入任意长度音频，内部按 SILERO_CHUNK_SIZE 累积处理。"""

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
        """处理一个 VAD chunk，并在状态变化时返回事件。"""

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
        """结束当前话语；过短则取消，足够长则输出 speech_end。"""

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
