from __future__ import annotations

import base64
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from reachy_mini import ReachyMini

from .utils import _audio_level_from_rms
from ..core.constants import (
    INPUT_SAMPLE_RATE,
    LIVE_CHUNK_BYTES,
    MIN_RECORDING_DURATION_SECONDS,
    MIN_RECORDING_RMS,
    ROBOT_MIC_POLL_SECONDS,
    ROBOT_MIC_READY_TIMEOUT_SECONDS,
)
from ..interaction import InteractionApiClient


@dataclass
class RecordedAudio:
    audio_base64: str
    sample_rate: int
    channels: int
    duration_seconds: float
    rms: float
    peak: float
    byte_count: int


@dataclass
class AudioLevel:
    is_recording: bool
    duration_seconds: float
    rms: float
    peak: float
    level: float


@dataclass
class LiveTranscript:
    text: str
    is_final: bool
    error: str | None = None


@dataclass(frozen=True)
class BehaviorTag:
    module: str
    tag_name: str
    key: str
    raw: str


@dataclass
class BehaviorTriggerResult:
    matched: bool
    module: str | None = None
    tag_name: str | None = None
    key: str | None = None
    url: str | None = None
    triggered: bool = False
    ok: bool = False
    status_code: int | None = None
    error: str | None = None


def _wait_for_robot_audio_sample(reachy_mini: ReachyMini) -> np.ndarray | None:
    start_time = time.time()
    while time.time() - start_time < ROBOT_MIC_READY_TIMEOUT_SECONDS:
        sample = reachy_mini.media.get_audio_sample()
        if sample is not None and len(sample) > 0:
            return sample
        time.sleep(ROBOT_MIC_POLL_SECONDS)
    return None


class InteractionLiveVoiceSession:
    def __init__(
        self,
        *,
        client: InteractionApiClient,
        interaction_session_id: str,
        workflow: str,
        live_session_id: str,
        sample_rate: int,
    ) -> None:
        self.client = client
        self.interaction_session_id = interaction_session_id
        self.workflow = workflow
        self.live_session_id = live_session_id
        self.session_id = live_session_id
        self.sample_rate = sample_rate
        self.queue: queue.Queue[bytes | None] = queue.Queue(maxsize=24)
        self.lock = threading.Lock()
        self.transcript = LiveTranscript(text="", is_final=False)
        self.created_at = time.time()
        self.queued_chunks = 0
        self.queued_bytes = 0
        self.posted_chunks = 0
        self.posted_bytes = 0
        self.accepted_bytes = 0
        self.failed_chunks = 0
        self.transcript_polls = 0
        self.last_error: str | None = None
        self.last_chunk_status: int | None = None
        self.thread = threading.Thread(target=self._send_loop, daemon=True)
        self.thread.start()

    @classmethod
    def start(
        cls,
        service_url: str,
        *,
        interaction_session_id: str,
        workflow: str,
        sample_rate: int,
        client_factory: Callable[[str], InteractionApiClient] = InteractionApiClient,
    ) -> "InteractionLiveVoiceSession":
        client = client_factory(service_url)
        data = client.live_start(
            interaction_session_id=interaction_session_id,
            workflow=workflow,  # type: ignore[arg-type]
            sample_rate=sample_rate,
            channels=1,
            audio_format="pcm",
        )
        live_session_id = data.get("live_session_id") or data.get("session_id")
        if not isinstance(live_session_id, str) or not live_session_id:
            raise RuntimeError("Interaction live start did not return a live_session_id.")
        return cls(
            client=client,
            interaction_session_id=interaction_session_id,
            workflow=workflow,
            live_session_id=live_session_id,
            sample_rate=int(data.get("sample_rate") or sample_rate),
        )

    def submit_pcm(self, pcm: bytes) -> None:
        if not pcm:
            return
        try:
            self.queue.put_nowait(bytes(pcm))
            with self.lock:
                self.queued_chunks += 1
                self.queued_bytes += len(pcm)
        except queue.Full:
            self._set_transcript(error="实时语音发送队列已满，部分音频被丢弃。")

    def get_transcript(self) -> LiveTranscript:
        if self.transcript.error:
            return self.transcript
        try:
            with self.lock:
                self.transcript_polls += 1
            data = self.client.live_transcript(
                interaction_session_id=self.interaction_session_id,
                workflow=self.workflow,  # type: ignore[arg-type]
                live_session_id=self.live_session_id,
            )
            self._set_transcript(
                text=str(data.get("transcript") or ""),
                is_final=bool(data.get("is_final")),
                error=data.get("error"),
            )
        except Exception as exc:
            self._set_transcript(error=f"读取实时字幕失败：{exc}")
        with self.lock:
            return self.transcript

    def finish_stream(self, *, tts_enabled: bool):
        self.queue.put(None)
        self.thread.join(timeout=5)
        for item in self.client.live_finish_stream(
            interaction_session_id=self.interaction_session_id,
            workflow=self.workflow,  # type: ignore[arg-type]
            live_session_id=self.live_session_id,
            tts_enabled=tts_enabled,
        ):
            data = item.data
            if item.event == "transcript":
                self._set_transcript(
                    text=str(data.get("transcript") or ""),
                    is_final=True,
                    error=None,
                )
            elif item.event == "done":
                self._set_transcript(
                    text=str(data.get("transcript") or self.transcript.text),
                    is_final=True,
                    error=None,
                )
            yield {"event": item.event, "data": data}

    def abort(self) -> None:
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            self.client.live_abort(
                interaction_session_id=self.interaction_session_id,
                workflow=self.workflow,  # type: ignore[arg-type]
                live_session_id=self.live_session_id,
            )
        except Exception:
            pass

    def debug_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "session_id": self.session_id,
                "live_session_id": self.live_session_id,
                "interaction_session_id": self.interaction_session_id,
                "workflow": self.workflow,
                "sample_rate": self.sample_rate,
                "age_seconds": round(time.time() - self.created_at, 2),
                "queue_size": self.queue.qsize(),
                "send_thread_alive": self.thread.is_alive(),
                "queued_chunks": self.queued_chunks,
                "queued_bytes": self.queued_bytes,
                "posted_chunks": self.posted_chunks,
                "posted_bytes": self.posted_bytes,
                "accepted_bytes": self.accepted_bytes,
                "failed_chunks": self.failed_chunks,
                "transcript_polls": self.transcript_polls,
                "transcript": self.transcript.text,
                "is_final": self.transcript.is_final,
                "last_error": self.last_error,
                "last_chunk_status": self.last_chunk_status,
            }

    def _set_transcript(
        self,
        text: str | None = None,
        *,
        is_final: bool | None = None,
        error: str | None = None,
    ) -> None:
        with self.lock:
            self.transcript = LiveTranscript(
                text=self.transcript.text if text is None else text,
                is_final=self.transcript.is_final if is_final is None else is_final,
                error=error,
            )
            self.last_error = error

    def _send_loop(self) -> None:
        buffer = bytearray()
        while True:
            item = self.queue.get()
            if item is None:
                if buffer:
                    self._post_chunk(bytes(buffer))
                return
            buffer.extend(item)
            while len(buffer) >= LIVE_CHUNK_BYTES:
                chunk = bytes(buffer[:LIVE_CHUNK_BYTES])
                del buffer[:LIVE_CHUNK_BYTES]
                if not self._post_chunk(chunk):
                    break

    def _post_chunk(self, chunk: bytes) -> bool:
        try:
            with self.lock:
                self.posted_chunks += 1
                self.posted_bytes += len(chunk)
            data = self.client.live_chunk(
                interaction_session_id=self.interaction_session_id,
                workflow=self.workflow,  # type: ignore[arg-type]
                live_session_id=self.live_session_id,
                audio_base64=base64.b64encode(chunk).decode("ascii"),
                is_final=False,
            )
            with self.lock:
                self.last_chunk_status = 200
            if not data.get("ok", False):
                with self.lock:
                    self.failed_chunks += 1
                self._set_transcript(error="Interaction live service rejected audio.")
                return False
            with self.lock:
                self.accepted_bytes += int(data.get("accepted_bytes") or 0)
            return True
        except Exception as exc:
            with self.lock:
                self.failed_chunks += 1
            self._set_transcript(error=f"发送实时音频失败：{exc}")
            return False


class RobotMicRecorder:
    def __init__(self, reachy_mini: ReachyMini) -> None:
        self.reachy_mini = reachy_mini
        self.lock = threading.Lock()
        self.samples: list[np.ndarray] = []
        self.captured_frames = 0
        self.empty_sample_count = 0
        self.sample_rate = INPUT_SAMPLE_RATE
        self.latest_level = AudioLevel(
            is_recording=False,
            duration_seconds=0.0,
            rms=0.0,
            peak=0.0,
            level=0.0,
        )
        self.thread: threading.Thread | None = None
        self.live_session: InteractionLiveVoiceSession | None = None
        self.live_transcript = LiveTranscript(text="", is_final=False)
        self.final_response: dict[str, Any] | None = None
        self.stop_event = threading.Event()
        self.is_recording = False
        self.is_processing_reply = False
        self.started_at = 0.0

    def start_interaction(
        self,
        *,
        service_url: str,
        interaction_session_id: str,
        workflow: str,
    ) -> None:
        with self.lock:
            if self.is_recording:
                raise RuntimeError("机器人麦克风已经在录音。")
            if self.is_processing_reply or self.live_session is not None:
                raise RuntimeError("上一轮回复还没有完成，请等机器人回复结束后再录音。")
            sample_rate = self.reachy_mini.media.get_input_audio_samplerate()
            if sample_rate < 0:
                raise RuntimeError(
                    "机器人音频系统未初始化。请使用启用了媒体功能的真实 "
                    "Reachy daemon 启动 app；--mockup-sim 会使用 --no-media，"
                    "不能录制机器人麦克风音频。"
                )
            self.samples = []
            self.captured_frames = 0
            self.empty_sample_count = 0
            self.sample_rate = sample_rate or INPUT_SAMPLE_RATE
            self.latest_level = AudioLevel(
                is_recording=True,
                duration_seconds=0.0,
                rms=0.0,
                peak=0.0,
                level=0.0,
            )
            self.stop_event.clear()
            self.live_session = None
            self.live_transcript = LiveTranscript(
                text="",
                is_final=False,
                error=None,
            )
            self.final_response = None
            self.is_recording = True
            self.started_at = 0.0

        live_session: InteractionLiveVoiceSession | None = None
        try:
            live_session = InteractionLiveVoiceSession.start(
                service_url,
                interaction_session_id=interaction_session_id,
                workflow=workflow,
                sample_rate=self.sample_rate,
            )
            with self.lock:
                self.live_session = live_session

            self.reachy_mini.media.start_recording()
            first_sample = _wait_for_robot_audio_sample(self.reachy_mini)
            if first_sample is None:
                raise RuntimeError(
                    "机器人麦克风在 "
                    f"{ROBOT_MIC_READY_TIMEOUT_SECONDS:.0f}s 内没有返回音频。"
                )

            with self.lock:
                self.started_at = time.time()
            self._capture_sample(first_sample)
            self.thread = threading.Thread(target=self._record_loop, daemon=True)
            self.thread.start()
        except Exception:
            self.reachy_mini.media.stop_recording()
            if live_session is not None:
                live_session.abort()
            with self.lock:
                if self.live_session is live_session:
                    self.live_session = None
                self.samples = []
                self.is_recording = False
                self.stop_event.set()
                self.thread = None
                self.latest_level = AudioLevel(
                    is_recording=False,
                    duration_seconds=0.0,
                    rms=0.0,
                    peak=0.0,
                    level=0.0,
                )
            raise

    def stop_interaction_for_stream(
        self,
    ) -> tuple[RecordedAudio, InteractionLiveVoiceSession]:
        recording = self._stop_recording()
        with self.lock:
            if not isinstance(self.live_session, InteractionLiveVoiceSession):
                raise RuntimeError("Interaction 实时语音会话不存在。")
            self.is_processing_reply = True
            return recording, self.live_session

    def finish_reply_processing(
        self,
        session: InteractionLiveVoiceSession,
    ) -> None:
        with self.lock:
            if self.live_session is session:
                self.live_session = None
            self.is_processing_reply = False

    def _stop_recording(self) -> RecordedAudio:
        with self.lock:
            if not self.is_recording:
                raise RuntimeError("机器人麦克风当前没有在录音。")
            self.is_recording = False
            self.stop_event.set()
            thread = self.thread

        if thread is not None:
            thread.join(timeout=2)
        self.reachy_mini.media.stop_recording()

        with self.lock:
            samples = list(self.samples)
            sample_rate = self.sample_rate
            self.samples = []
            self.thread = None

        if not samples:
            if self.live_session is not None:
                self.live_session.abort()
                self.live_session = None
            raise RuntimeError("没有捕获到机器人麦克风音频。")

        audio = np.concatenate(samples, axis=0)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        pcm = np.clip(audio, -1.0, 1.0)
        duration_seconds = float(pcm.shape[0] / sample_rate)
        rms = float(np.sqrt(np.mean(np.square(pcm, dtype=np.float64))))
        peak = float(np.max(np.abs(pcm)))
        if duration_seconds < MIN_RECORDING_DURATION_SECONDS:
            if self.live_session is not None:
                self.live_session.abort()
                self.live_session = None
            raise RuntimeError(
                f"录音太短（{duration_seconds:.2f}s）。请按住久一点，"
                "确认开始录音后再说话。"
            )
        if rms < MIN_RECORDING_RMS:
            if self.live_session is not None:
                self.live_session.abort()
                self.live_session = None
            raise RuntimeError(
                f"机器人麦克风音频几乎是静音（RMS={rms:.5f}，"
                f"峰值={peak:.5f}）。请检查机器人麦克风或输入设备。"
            )
        pcm_i16 = (pcm * 32767.0).astype("<i2")
        audio_bytes = pcm_i16.tobytes()
        recording = RecordedAudio(
            audio_base64=base64.b64encode(audio_bytes).decode("ascii"),
            sample_rate=sample_rate,
            channels=1,
            duration_seconds=duration_seconds,
            rms=rms,
            peak=peak,
            byte_count=len(audio_bytes),
        )
        with self.lock:
            self.latest_level = AudioLevel(
                is_recording=False,
                duration_seconds=duration_seconds,
                rms=rms,
                peak=peak,
                level=_audio_level_from_rms(rms),
            )
        return recording

    def get_level(self) -> AudioLevel:
        with self.lock:
            return self.latest_level

    def get_transcript(self) -> LiveTranscript:
        session = self.live_session
        if session is not None:
            self.live_transcript = session.get_transcript()
        return self.live_transcript

    def debug_snapshot(self) -> dict[str, Any]:
        with self.lock:
            latest_level = self.latest_level
            samples_count = len(self.samples)
            captured_frames = self.captured_frames
            empty_sample_count = self.empty_sample_count
            sample_rate = self.sample_rate
            is_recording = self.is_recording
            is_processing_reply = self.is_processing_reply
            live_session = self.live_session
            transcript = self.live_transcript
        return {
            "is_recording": is_recording,
            "is_processing_reply": is_processing_reply,
            "samples_count": samples_count,
            "captured_frames": captured_frames,
            "empty_sample_count": empty_sample_count,
            "sample_rate": sample_rate,
            "level": {
                "duration_seconds": latest_level.duration_seconds,
                "rms": latest_level.rms,
                "peak": latest_level.peak,
                "level": latest_level.level,
            },
            "live_transcript": {
                "text": transcript.text,
                "is_final": transcript.is_final,
                "error": transcript.error,
            },
            "live_session": live_session.debug_snapshot()
            if live_session is not None
            else None,
        }

    def _record_loop(self) -> None:
        while not self.stop_event.is_set():
            sample = self.reachy_mini.media.get_audio_sample()
            if sample is not None and len(sample) > 0:
                self._capture_sample(sample)
            else:
                with self.lock:
                    self.empty_sample_count += 1
                time.sleep(ROBOT_MIC_POLL_SECONDS)

    def _capture_sample(self, sample: np.ndarray) -> None:
        mono = sample
        if mono.ndim == 2:
            mono = mono.mean(axis=1)
        mono = np.clip(mono, -1.0, 1.0)
        rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
        peak = float(np.max(np.abs(mono)))
        pcm_i16 = (mono * 32767.0).astype("<i2")
        if self.live_session is not None:
            self.live_session.submit_pcm(pcm_i16.tobytes())
        with self.lock:
            self.samples.append(np.array(sample, copy=True))
            self.captured_frames += int(mono.shape[0])
            self.latest_level = AudioLevel(
                is_recording=self.is_recording,
                duration_seconds=max(0.0, time.time() - self.started_at),
                rms=rms,
                peak=peak,
                level=_audio_level_from_rms(rms),
            )


class RobotMicPlaybackTester:
    def __init__(self, reachy_mini: ReachyMini) -> None:
        self.reachy_mini = reachy_mini
        self.lock = threading.Lock()
        self.samples: list[np.ndarray] = []
        self.captured_frames = 0
        self.empty_sample_count = 0
        self.sample_rate = INPUT_SAMPLE_RATE
        self.latest_level = AudioLevel(
            is_recording=False,
            duration_seconds=0.0,
            rms=0.0,
            peak=0.0,
            level=0.0,
        )
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.is_recording = False
        self.started_at = 0.0

    def start(self) -> None:
        with self.lock:
            if self.is_recording:
                raise RuntimeError("机器人麦克风回放测试已经在录音。")
            sample_rate = self.reachy_mini.media.get_input_audio_samplerate()
            if sample_rate < 0:
                raise RuntimeError(
                    "机器人音频系统未初始化。请使用启用了媒体功能的真实 "
                    "Reachy daemon 启动 app；--mockup-sim 会使用 --no-media，"
                    "不能录制机器人麦克风音频。"
                )
            self.samples = []
            self.captured_frames = 0
            self.empty_sample_count = 0
            self.sample_rate = sample_rate or INPUT_SAMPLE_RATE
            self.latest_level = AudioLevel(
                is_recording=True,
                duration_seconds=0.0,
                rms=0.0,
                peak=0.0,
                level=0.0,
            )
            self.stop_event.clear()
            self.is_recording = True
            self.started_at = 0.0

        try:
            self.reachy_mini.media.start_recording()
            first_sample = _wait_for_robot_audio_sample(self.reachy_mini)
            if first_sample is None:
                raise RuntimeError(
                    "机器人麦克风在 "
                    f"{ROBOT_MIC_READY_TIMEOUT_SECONDS:.0f}s 内没有返回音频。"
                )
            with self.lock:
                self.started_at = time.time()
            self._capture_sample(first_sample)
            self.thread = threading.Thread(target=self._record_loop, daemon=True)
            self.thread.start()
        except Exception:
            self.reachy_mini.media.stop_recording()
            with self.lock:
                self.samples = []
                self.is_recording = False
                self.stop_event.set()
                self.thread = None
                self.latest_level = AudioLevel(
                    is_recording=False,
                    duration_seconds=0.0,
                    rms=0.0,
                    peak=0.0,
                    level=0.0,
                )
            raise

    def stop(self) -> RecordedAudio:
        with self.lock:
            if not self.is_recording:
                raise RuntimeError("机器人麦克风回放测试当前没有在录音。")
            self.is_recording = False
            self.stop_event.set()
            thread = self.thread
            sample_rate = self.sample_rate

        if thread is not None:
            thread.join(timeout=2)
        self.reachy_mini.media.stop_recording()

        with self.lock:
            samples = list(self.samples)
            self.samples = []
            self.thread = None

        if not samples:
            raise RuntimeError("没有捕获到机器人麦克风音频。")

        audio = np.concatenate(samples, axis=0)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        pcm = np.clip(audio, -1.0, 1.0)
        duration_seconds = float(pcm.shape[0] / sample_rate)
        rms = float(np.sqrt(np.mean(np.square(pcm, dtype=np.float64))))
        peak = float(np.max(np.abs(pcm)))
        pcm_i16 = (pcm * 32767.0).astype("<i2")
        audio_bytes = pcm_i16.tobytes()
        recording = RecordedAudio(
            audio_base64=base64.b64encode(audio_bytes).decode("ascii"),
            sample_rate=sample_rate,
            channels=1,
            duration_seconds=duration_seconds,
            rms=rms,
            peak=peak,
            byte_count=len(audio_bytes),
        )
        with self.lock:
            self.latest_level = AudioLevel(
                is_recording=False,
                duration_seconds=duration_seconds,
                rms=rms,
                peak=peak,
                level=_audio_level_from_rms(rms),
            )
        return recording

    def get_level(self) -> AudioLevel:
        with self.lock:
            return self.latest_level

    def debug_snapshot(self) -> dict[str, Any]:
        with self.lock:
            latest_level = self.latest_level
            return {
                "is_recording": self.is_recording,
                "samples_count": len(self.samples),
                "captured_frames": self.captured_frames,
                "empty_sample_count": self.empty_sample_count,
                "sample_rate": self.sample_rate,
                "level": {
                    "duration_seconds": latest_level.duration_seconds,
                    "rms": latest_level.rms,
                    "peak": latest_level.peak,
                    "level": latest_level.level,
                },
            }

    def _record_loop(self) -> None:
        while not self.stop_event.is_set():
            sample = self.reachy_mini.media.get_audio_sample()
            if sample is not None and len(sample) > 0:
                self._capture_sample(sample)
            else:
                with self.lock:
                    self.empty_sample_count += 1
                time.sleep(ROBOT_MIC_POLL_SECONDS)

    def _capture_sample(self, sample: np.ndarray) -> None:
        mono = sample
        if mono.ndim == 2:
            mono = mono.mean(axis=1)
        mono = np.clip(mono, -1.0, 1.0)
        rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
        peak = float(np.max(np.abs(mono)))
        with self.lock:
            self.samples.append(np.array(sample, copy=True))
            self.captured_frames += int(mono.shape[0])
            self.latest_level = AudioLevel(
                is_recording=self.is_recording,
                duration_seconds=max(0.0, time.time() - self.started_at),
                rms=rms,
                peak=peak,
                level=_audio_level_from_rms(rms),
            )
