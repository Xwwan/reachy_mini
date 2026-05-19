import argparse
import base64
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import numpy as np
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from reachy_mini import ReachyMini, ReachyMiniApp

try:
    import yaml
except ImportError:  # pragma: no cover - dependency is declared in pyproject.
    yaml = None


DEFAULT_SERVICE_URL = "http://127.0.0.1:12312"
DEFAULT_CONVERSATION_ID = "reachy-mini-voice"
DEFAULT_EMOJI_SERVICE_URL = "http://127.0.0.1:8001"
DEFAULT_BEHAVIOR_CONFIG_FILE = Path(__file__).resolve().parent / "behavior_config.yaml"
DEFAULT_EMOJI_CONFIG_FILE = Path(__file__).resolve().parent / "emoji_config.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
DEFAULT_ROBOT_PORT = 8000
MIN_RECORDING_DURATION_SECONDS = 0.3
MIN_RECORDING_RMS = 0.0002
LIVE_CHUNK_BYTES = 5120
ROBOT_MIC_READY_TIMEOUT_SECONDS = 1.0
ROBOT_MIC_POLL_SECONDS = 0.005


@dataclass
class RobotJob:
    audio_base64: str | None = None
    audio_sample_rate: int = OUTPUT_SAMPLE_RATE
    action_signal: str | None = None
    action_config: dict[str, Any] | None = None
    done_event: threading.Event | None = None


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


class SettingsPayload(BaseModel):
    service_url: str | None = None
    conversation_id: str | None = None
    tts_sample_rate: int | None = None


class VoiceChatPayload(BaseModel):
    audio_base64: str
    audio_format: str = "pcm"
    conversation_id: str | None = None
    tts_enabled: bool = True


class TextChatPayload(BaseModel):
    text: str
    conversation_id: str | None = None
    tts_enabled: bool = True


class VolumePayload(BaseModel):
    volume: int = Field(..., ge=0, le=100)


class LocalMicChunkPayload(BaseModel):
    session_id: str
    audio_base64: str
    is_final: bool = False


class LocalMicFinishStreamPayload(BaseModel):
    session_id: str
    conversation_id: str | None = None
    tts_enabled: bool = True


class LocalMicAbortPayload(BaseModel):
    session_id: str


class LiveVoiceSession:
    def __init__(
        self,
        *,
        service_url: str,
        session_id: str,
        sample_rate: int,
    ) -> None:
        self.service_url = _normalize_service_url(service_url)
        self.session_id = session_id
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
    def start(cls, service_url: str, *, sample_rate: int) -> "LiveVoiceSession":
        normalized = _normalize_service_url(service_url)
        response = requests.post(
            urljoin(normalized, "/voice/live/start"),
            json={
                "sample_rate": sample_rate,
                "channels": 1,
                "audio_format": "pcm",
            },
            timeout=10,
        )
        data = _json_or_error(response)
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("实时语音服务没有返回有效 session_id。")
        return cls(
            service_url=normalized,
            session_id=session_id,
            sample_rate=sample_rate,
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
            response = requests.get(
                urljoin(self.service_url, "/voice/live/transcript"),
                params={"session_id": self.session_id},
                timeout=2,
            )
            data = _json_or_error(response)
            self._set_transcript(
                text=str(data.get("transcript") or ""),
                is_final=bool(data.get("is_final")),
                error=data.get("error"),
            )
        except Exception as exc:
            self._set_transcript(error=f"读取实时字幕失败：{exc}")
        with self.lock:
            return self.transcript

    def finish(self, *, conversation_id: str, tts_enabled: bool) -> dict[str, Any]:
        self.queue.put(None)
        self.thread.join(timeout=5)
        response = requests.post(
            urljoin(self.service_url, "/voice/live/finish"),
            json={
                "session_id": self.session_id,
                "conversation_id": conversation_id,
                "tts_enabled": tts_enabled,
            },
            timeout=90,
        )
        data = _json_or_error(response)
        self._set_transcript(
            text=str(data.get("transcript") or ""),
            is_final=True,
            error=None,
        )
        return data

    def finish_stream(
        self,
        *,
        conversation_id: str,
        tts_enabled: bool,
    ):
        self.queue.put(None)
        self.thread.join(timeout=5)
        response = requests.post(
            urljoin(self.service_url, "/voice/live/finish-stream"),
            json={
                "session_id": self.session_id,
                "conversation_id": conversation_id,
                "tts_enabled": tts_enabled,
            },
            stream=True,
            timeout=(10, 120),
        )
        if response.status_code == 404:
            response.close()
            data = self._finish_json(
                conversation_id=conversation_id,
                tts_enabled=tts_enabled,
            )
            transcript = str(data.get("transcript") or "")
            if transcript:
                yield {
                    "event": "transcript",
                    "data": {
                        "session_id": self.session_id,
                        "conversation_id": conversation_id,
                        "transcript": transcript,
                    },
                }
            reply = str(data.get("reply") or "")
            if reply:
                yield {"event": "delta", "data": {"delta": reply}}
            yield {"event": "done", "data": data}
            return

        try:
            for item in _iter_sse_events(response):
                data = item.get("data", {})
                if item.get("event") == "transcript":
                    self._set_transcript(
                        text=str(data.get("transcript") or ""),
                        is_final=True,
                        error=None,
                    )
                elif item.get("event") == "done":
                    self._set_transcript(
                        text=str(data.get("transcript") or self.transcript.text),
                        is_final=True,
                        error=None,
                    )
                yield item
        finally:
            response.close()

    def abort(self) -> None:
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            requests.post(
                urljoin(self.service_url, "/voice/live/abort"),
                json={"session_id": self.session_id},
                timeout=5,
            )
        except requests.RequestException:
            pass

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
            response = requests.post(
                urljoin(self.service_url, "/voice/live/chunk"),
                json={
                    "session_id": self.session_id,
                    "audio_base64": base64.b64encode(chunk).decode("ascii"),
                    "is_final": False,
                },
                timeout=5,
            )
            with self.lock:
                self.last_chunk_status = response.status_code
            data = _json_or_error(response)
            if not data.get("ok", False):
                with self.lock:
                    self.failed_chunks += 1
                self._set_transcript(error="实时语音服务拒绝了音频块。")
                return False
            with self.lock:
                self.accepted_bytes += int(data.get("accepted_bytes") or 0)
            return True
        except Exception as exc:
            with self.lock:
                self.failed_chunks += 1
            self._set_transcript(error=f"发送实时音频失败：{exc}")
            return False

    def debug_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "session_id": self.session_id,
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

    def _finish_json(self, *, conversation_id: str, tts_enabled: bool) -> dict[str, Any]:
        response = requests.post(
            urljoin(self.service_url, "/voice/live/finish"),
            json={
                "session_id": self.session_id,
                "conversation_id": conversation_id,
                "tts_enabled": tts_enabled,
            },
            timeout=90,
        )
        data = _json_or_error(response)
        self._set_transcript(
            text=str(data.get("transcript") or ""),
            is_final=True,
            error=None,
        )
        return data


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
        self.live_session: LiveVoiceSession | None = None
        self.live_transcript = LiveTranscript(text="", is_final=False)
        self.final_response: dict[str, Any] | None = None
        self.stop_event = threading.Event()
        self.is_recording = False
        self.is_processing_reply = False
        self.started_at = 0.0

    def start(self, *, service_url: str) -> None:
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

        live_session: LiveVoiceSession | None = None
        try:
            live_session = LiveVoiceSession.start(
                service_url,
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

    def stop(self, *, conversation_id: str, tts_enabled: bool) -> RecordedAudio:
        recording = self._stop_recording()
        if self.live_session is not None:
            try:
                self.final_response = self.live_session.finish(
                    conversation_id=conversation_id,
                    tts_enabled=tts_enabled,
                )
                self.live_transcript = LiveTranscript(
                    text=str(self.final_response.get("transcript") or ""),
                    is_final=True,
                    error=None,
                )
            finally:
                self.live_session = None
        return recording

    def stop_for_stream(self) -> tuple[RecordedAudio, LiveVoiceSession]:
        recording = self._stop_recording()
        with self.lock:
            if self.live_session is None:
                raise RuntimeError("实时语音会话不存在。")
            self.is_processing_reply = True
            return recording, self.live_session

    def finish_reply_processing(self, session: LiveVoiceSession) -> None:
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


class ReachyDialogueApp(ReachyMiniApp):
    custom_app_url: str | None = "http://0.0.0.0:8042"
    request_media_backend: str | None = None

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event):
        assert self.settings_app is not None

        settings_lock = threading.Lock()
        settings: dict[str, Any] = {
            "service_url": os.environ.get(
                "REACHY_DIALOGUE_SERVICE_URL", DEFAULT_SERVICE_URL
            ),
            "conversation_id": os.environ.get(
                "REACHY_DIALOGUE_CONVERSATION_ID", DEFAULT_CONVERSATION_ID
            ),
            "tts_sample_rate": int(
                os.environ.get("REACHY_DIALOGUE_TTS_SAMPLE_RATE", OUTPUT_SAMPLE_RATE)
            ),
        }
        jobs: queue.Queue[RobotJob] = queue.Queue()
        recorder = RobotMicRecorder(reachy_mini)
        playback_tester = RobotMicPlaybackTester(reachy_mini)
        behavior_config = _load_behavior_config()
        _register_local_mic_routes(
            self.settings_app,
            settings,
            settings_lock,
            behavior_config=behavior_config,
        )

        @self.settings_app.get("/api/settings")
        def get_settings() -> dict[str, Any]:
            with settings_lock:
                return dict(settings)

        @self.settings_app.get("/api/emoji-config")
        def get_emoji_config() -> dict[str, Any]:
            return _public_emoji_config(behavior_config)

        @self.settings_app.get("/api/behavior-config")
        def get_behavior_config() -> dict[str, Any]:
            return _public_behavior_config(behavior_config)

        @self.settings_app.post("/api/settings")
        def update_settings(payload: SettingsPayload) -> dict[str, Any]:
            with settings_lock:
                if payload.service_url is not None:
                    settings["service_url"] = _normalize_service_url(
                        payload.service_url
                    )
                if payload.conversation_id is not None:
                    settings["conversation_id"] = payload.conversation_id.strip()
                if payload.tts_sample_rate is not None:
                    settings["tts_sample_rate"] = payload.tts_sample_rate
                return dict(settings)

        @self.settings_app.get("/api/health")
        def health() -> dict[str, Any]:
            current = _snapshot(settings, settings_lock)
            try:
                response = requests.get(
                    urljoin(current["service_url"], "/healthz"),
                    timeout=3,
                )
                return {
                    "ok": response.ok,
                    "status_code": response.status_code,
                    "service_url": current["service_url"],
                }
            except requests.RequestException as exc:
                return {
                    "ok": False,
                    "error": str(exc),
                    "service_url": current["service_url"],
                }

        @self.settings_app.get("/api/app-mode")
        def app_mode() -> dict[str, Any]:
            return {"web_only": False}

        @self.settings_app.post("/api/voice-chat")
        def voice_chat(payload: VoiceChatPayload) -> dict[str, Any]:
            current = _snapshot(settings, settings_lock)
            conversation_id = (
                payload.conversation_id or current["conversation_id"]
            ).strip()
            if not conversation_id:
                conversation_id = DEFAULT_CONVERSATION_ID

            body = {
                "conversation_id": conversation_id,
                "audio_base64": payload.audio_base64,
                "audio_format": payload.audio_format,
                "tts_enabled": payload.tts_enabled,
            }
            response = requests.post(
                urljoin(current["service_url"], "/voice/chat"),
                json=body,
                timeout=90,
            )
            data = _json_or_error(response)
            if response.ok:
                behavior_results = _trigger_behaviors_from_text(
                    str(data.get("reply") or ""),
                    behavior_config,
                )
                if behavior_results:
                    data["behavior_triggers"] = [
                        _behavior_result_payload(result) for result in behavior_results
                    ]
                    emoji_result = _first_module_result(behavior_results, "emoji")
                    if emoji_result is not None:
                        data["emoji_trigger"] = _emoji_result_payload(emoji_result)
                action_signal = _first_ok_module_key(behavior_results, "action")
                jobs.put(
                    RobotJob(
                        audio_base64=data.get("audio_base64"),
                        audio_sample_rate=int(current["tts_sample_rate"]),
                        action_signal=action_signal,
                        action_config=_module_config(behavior_config, "action"),
                    )
                )
            return data

        _register_text_chat_routes(
            self.settings_app,
            settings,
            settings_lock,
            behavior_config=behavior_config,
            jobs=jobs,
        )

        @self.settings_app.get("/api/audio-volume")
        def get_audio_volume() -> dict[str, Any]:
            return {
                "speaker": _daemon_volume_request(
                    reachy_mini,
                    "GET",
                    "/api/volume/current",
                ),
                "microphone": _daemon_volume_request(
                    reachy_mini,
                    "GET",
                    "/api/volume/microphone/current",
                ),
            }

        @self.settings_app.post("/api/audio-volume/speaker")
        def set_speaker_volume(payload: VolumePayload) -> dict[str, Any]:
            return _daemon_volume_request(
                reachy_mini,
                "POST",
                "/api/volume/set",
                volume=payload.volume,
            )

        @self.settings_app.post("/api/audio-volume/microphone")
        def set_microphone_volume(payload: VolumePayload) -> dict[str, Any]:
            return _daemon_volume_request(
                reachy_mini,
                "POST",
                "/api/volume/microphone/set",
                volume=payload.volume,
            )

        @self.settings_app.post("/api/robot-mic/start")
        def start_robot_mic() -> dict[str, Any]:
            current = _snapshot(settings, settings_lock)
            if playback_tester.get_level().is_recording:
                raise HTTPException(
                    status_code=409,
                    detail="机器人麦克风回放测试正在录音，请先停止测试。",
                )
            try:
                recorder.start(service_url=current["service_url"])
            except Exception as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return {
                "ok": True,
                "sample_rate": reachy_mini.media.get_input_audio_samplerate(),
                "channels": reachy_mini.media.get_input_channels(),
            }

        @self.settings_app.post("/api/robot-mic/stop")
        def stop_robot_mic() -> dict[str, Any]:
            current = _snapshot(settings, settings_lock)
            conversation_id = current["conversation_id"].strip() or DEFAULT_CONVERSATION_ID
            try:
                recording = recorder.stop(
                    conversation_id=conversation_id,
                    tts_enabled=True,
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            transcript = recorder.get_transcript()
            final_response = recorder.final_response or {}
            if final_response:
                jobs.put(
                    RobotJob(
                        audio_base64=final_response.get("audio_base64"),
                        audio_sample_rate=int(current["tts_sample_rate"]),
                    )
                )
            return {
                "audio_base64": recording.audio_base64,
                "audio_format": "pcm",
                "sample_rate": recording.sample_rate,
                "channels": recording.channels,
                "duration_seconds": recording.duration_seconds,
                "rms": recording.rms,
                "peak": recording.peak,
                "byte_count": recording.byte_count,
                "live_transcript": transcript.text,
                "live_transcript_final": transcript.is_final,
                "live_transcript_error": transcript.error,
                "conversation_id": final_response.get("conversation_id", conversation_id),
                "request_id": final_response.get("request_id"),
                "turn_id": final_response.get("turn_id"),
                "transcript": final_response.get("transcript") or transcript.text,
                "reply": final_response.get("reply"),
                "retrieval_status": final_response.get("retrieval_status"),
                "retrieved_memory_ids": final_response.get("retrieved_memory_ids", []),
                "response_audio_base64": final_response.get("audio_base64"),
                "response_audio_format": final_response.get("audio_format"),
            }

        @self.settings_app.post("/api/robot-mic/stop-stream")
        def stop_robot_mic_stream() -> StreamingResponse:
            current = _snapshot(settings, settings_lock)
            conversation_id = current["conversation_id"].strip() or DEFAULT_CONVERSATION_ID
            try:
                recording, session = recorder.stop_for_stream()
            except Exception as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

            def event_stream():
                audio_chunks: list[bytes] = []
                audio_sample_rate = int(current["tts_sample_rate"])
                try:
                    yield _sse_frame(
                        "recording",
                        {
                            "audio_format": "pcm",
                            "sample_rate": recording.sample_rate,
                            "channels": recording.channels,
                            "duration_seconds": recording.duration_seconds,
                            "rms": recording.rms,
                            "peak": recording.peak,
                            "byte_count": recording.byte_count,
                        },
                    )
                    yield _sse_frame("debug", recorder.debug_snapshot())
                    for item in session.finish_stream(
                        conversation_id=conversation_id,
                        tts_enabled=True,
                    ):
                        event = str(item.get("event") or "message")
                        data = item.get("data") or {}
                        if event == "audio":
                            audio_base64 = data.get("audio_base64")
                            if isinstance(audio_base64, str) and audio_base64:
                                audio_chunks.append(base64.b64decode(audio_base64))
                            audio_sample_rate = int(
                                data.get("sample_rate") or audio_sample_rate
                            )
                        if event == "done":
                            recorder.final_response = dict(data)
                            recorder.live_transcript = LiveTranscript(
                                text=str(data.get("transcript") or ""),
                                is_final=True,
                                error=None,
                            )
                            behavior_results = _trigger_behaviors_from_text(
                                str(data.get("reply") or ""),
                                behavior_config,
                            )
                            for result in behavior_results:
                                yield _sse_frame(
                                    "behavior",
                                    _behavior_result_payload(result),
                                )
                            action_signal = _first_ok_module_key(
                                behavior_results,
                                "action",
                            )
                            audio_base64 = data.get("audio_base64")
                            if not audio_base64 and audio_chunks:
                                audio_base64 = base64.b64encode(
                                    b"".join(audio_chunks)
                                ).decode("ascii")
                            playback_done = threading.Event()
                            jobs.put(
                                RobotJob(
                                    audio_base64=audio_base64,
                                    audio_sample_rate=audio_sample_rate,
                                    action_signal=action_signal,
                                    action_config=_module_config(
                                        behavior_config,
                                        "action",
                                    ),
                                    done_event=playback_done,
                                )
                            )
                            yield _sse_frame(event, data)
                            playback_done.wait(timeout=120)
                            yield _sse_frame("playback_done", {"ok": True})
                            continue
                        yield _sse_frame(event, data)
                except Exception as exc:
                    yield _sse_frame(
                        "error",
                        {"message": str(exc) or exc.__class__.__name__},
                    )
                finally:
                    recorder.finish_reply_processing(session)

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream; charset=utf-8",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        @self.settings_app.get("/api/robot-mic/level")
        def get_robot_mic_level() -> dict[str, Any]:
            level = recorder.get_level()
            return {
                "is_recording": level.is_recording,
                "duration_seconds": level.duration_seconds,
                "rms": level.rms,
                "peak": level.peak,
                "level": level.level,
            }

        @self.settings_app.get("/api/robot-mic/transcript")
        def get_robot_mic_transcript() -> dict[str, Any]:
            transcript = recorder.get_transcript()
            return {
                "text": transcript.text,
                "is_final": transcript.is_final,
                "error": transcript.error,
            }

        @self.settings_app.get("/api/robot-mic/debug")
        def get_robot_mic_debug() -> dict[str, Any]:
            return recorder.debug_snapshot()

        @self.settings_app.post("/api/robot-mic/playback-test/start")
        def start_robot_mic_playback_test() -> dict[str, Any]:
            recorder_state = recorder.debug_snapshot()
            if recorder_state["is_recording"] or recorder_state["is_processing_reply"]:
                raise HTTPException(
                    status_code=409,
                    detail="语音对话正在录音或回复中，请等当前流程结束后再测试回放。",
                )
            try:
                playback_tester.start()
            except Exception as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return {
                "ok": True,
                "sample_rate": reachy_mini.media.get_input_audio_samplerate(),
                "channels": reachy_mini.media.get_input_channels(),
            }

        @self.settings_app.post("/api/robot-mic/playback-test/stop")
        def stop_robot_mic_playback_test() -> dict[str, Any]:
            try:
                recording = playback_tester.stop()
            except Exception as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            playback_done = threading.Event()
            jobs.put(
                RobotJob(
                    audio_base64=recording.audio_base64,
                    audio_sample_rate=recording.sample_rate,
                    done_event=playback_done,
                )
            )
            playback_timeout = min(120.0, max(5.0, recording.duration_seconds + 5.0))
            playback_finished = playback_done.wait(timeout=playback_timeout)
            return {
                "ok": True,
                "audio_format": "pcm",
                "sample_rate": recording.sample_rate,
                "channels": recording.channels,
                "duration_seconds": recording.duration_seconds,
                "rms": recording.rms,
                "peak": recording.peak,
                "byte_count": recording.byte_count,
                "playback_finished": playback_finished,
            }

        @self.settings_app.get("/api/robot-mic/playback-test/level")
        def get_robot_mic_playback_test_level() -> dict[str, Any]:
            level = playback_tester.get_level()
            return {
                "is_recording": level.is_recording,
                "duration_seconds": level.duration_seconds,
                "rms": level.rms,
                "peak": level.peak,
                "level": level.level,
            }

        while not stop_event.is_set():
            try:
                job = jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                _handle_robot_job(reachy_mini, job)
            finally:
                if job.done_event is not None:
                    job.done_event.set()


def _snapshot(
    settings: dict[str, Any], settings_lock: threading.Lock
) -> dict[str, Any]:
    with settings_lock:
        current = dict(settings)
    current["service_url"] = _normalize_service_url(current["service_url"])
    return current


def _default_settings() -> dict[str, Any]:
    return {
        "service_url": os.environ.get("REACHY_DIALOGUE_SERVICE_URL", DEFAULT_SERVICE_URL),
        "conversation_id": os.environ.get(
            "REACHY_DIALOGUE_CONVERSATION_ID", DEFAULT_CONVERSATION_ID
        ),
        "tts_sample_rate": int(
            os.environ.get("REACHY_DIALOGUE_TTS_SAMPLE_RATE", OUTPUT_SAMPLE_RATE)
        ),
    }


def _register_settings_routes(
    app: FastAPI,
    settings: dict[str, Any],
    settings_lock: threading.Lock,
) -> None:
    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        with settings_lock:
            return dict(settings)

    @app.post("/api/settings")
    def update_settings(payload: SettingsPayload) -> dict[str, Any]:
        with settings_lock:
            if payload.service_url is not None:
                settings["service_url"] = _normalize_service_url(payload.service_url)
            if payload.conversation_id is not None:
                settings["conversation_id"] = payload.conversation_id.strip()
            if payload.tts_sample_rate is not None:
                settings["tts_sample_rate"] = payload.tts_sample_rate
            return dict(settings)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            response = requests.get(
                urljoin(current["service_url"], "/healthz"),
                timeout=3,
            )
            return {
                "ok": response.ok,
                "status_code": response.status_code,
                "service_url": current["service_url"],
            }
        except requests.RequestException as exc:
            return {
                "ok": False,
                "error": str(exc),
                "service_url": current["service_url"],
            }


def _register_text_chat_routes(
    app: FastAPI,
    settings: dict[str, Any],
    settings_lock: threading.Lock,
    *,
    behavior_config: dict[str, Any],
    jobs: queue.Queue[RobotJob] | None = None,
) -> None:
    @app.post("/api/text-chat-stream")
    def text_chat_stream(payload: TextChatPayload) -> StreamingResponse:
        current = _snapshot(settings, settings_lock)
        conversation_id = (
            payload.conversation_id or current["conversation_id"]
        ).strip()
        if not conversation_id:
            conversation_id = DEFAULT_CONVERSATION_ID
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="文本不能为空。")

        def event_stream():
            upstream: requests.Response | None = None

            def finish_events(done_payload: dict[str, Any], reply: str):
                behavior_results = _trigger_behaviors_from_text(
                    reply,
                    behavior_config,
                )
                for result in behavior_results:
                    yield _sse_frame(
                        "behavior",
                        _behavior_result_payload(result),
                    )
                done_payload.setdefault("conversation_id", conversation_id)
                done_payload.setdefault("transcript", text)
                done_payload.setdefault("reply", reply)
                if jobs is None:
                    yield _sse_frame("done", done_payload)
                    yield _sse_frame("playback_done", {"ok": True, "skipped": True})
                    return

                action_signal = _first_ok_module_key(
                    behavior_results,
                    "action",
                )
                audio_base64 = (
                    done_payload.get("audio_base64")
                    or done_payload.get("response_audio_base64")
                    or done_payload.get("tts_audio_base64")
                )
                audio_sample_rate = int(
                    done_payload.get("sample_rate")
                    or done_payload.get("audio_sample_rate")
                    or current["tts_sample_rate"]
                )
                playback_done = threading.Event()
                jobs.put(
                    RobotJob(
                        audio_base64=audio_base64,
                        audio_sample_rate=audio_sample_rate,
                        action_signal=action_signal,
                        action_config=_module_config(behavior_config, "action"),
                        done_event=playback_done,
                    )
                )
                yield _sse_frame("done", done_payload)
                playback_done.wait(timeout=120)
                yield _sse_frame("playback_done", {"ok": True})

            try:
                yield _sse_frame(
                    "transcript",
                    {
                        "conversation_id": conversation_id,
                        "transcript": text,
                    },
                )
                upstream = requests.post(
                    urljoin(current["service_url"], "/chat/stream"),
                    json={
                        "conversation_id": conversation_id,
                        "message": text,
                        "tts_enabled": payload.tts_enabled,
                    },
                    stream=True,
                    timeout=(10, 120),
                )
                if upstream.status_code == 404:
                    upstream.close()
                    upstream = None
                    data = _post_text_chat(
                        service_url=current["service_url"],
                        conversation_id=conversation_id,
                        text=text,
                        tts_enabled=payload.tts_enabled,
                    )
                    reply = _reply_text_from_payload(data)
                    if reply:
                        yield _sse_frame("delta", {"delta": reply})
                    yield from finish_events(dict(data), reply)
                    return

                reply_parts: list[str] = []
                for item in _iter_sse_events(upstream):
                    event = str(item.get("event") or "message")
                    data = item.get("data") or {}
                    if event == "delta":
                        delta = str(data.get("delta") or "")
                        if delta:
                            reply_parts.append(delta)
                        yield _sse_frame(event, data)
                        continue
                    if event == "done":
                        reply = _reply_text_from_payload(data) or "".join(reply_parts)
                        yield from finish_events(dict(data), reply)
                        return
                    yield _sse_frame(event, data)
                    if event == "error":
                        return

                reply = "".join(reply_parts)
                if reply:
                    yield from finish_events(
                        {
                            "conversation_id": conversation_id,
                            "transcript": text,
                            "reply": reply,
                        },
                        reply,
                    )
            except HTTPException as exc:
                yield _sse_frame("error", {"message": str(exc.detail)})
            except Exception as exc:
                yield _sse_frame(
                    "error",
                    {"message": str(exc) or exc.__class__.__name__},
                )
            finally:
                if upstream is not None:
                    upstream.close()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


def _register_local_mic_routes(
    app: FastAPI,
    settings: dict[str, Any],
    settings_lock: threading.Lock,
    *,
    behavior_config: dict[str, Any] | None = None,
) -> None:
    @app.post("/api/local-mic/start")
    def local_mic_start() -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        response = requests.post(
            urljoin(current["service_url"], "/voice/live/start"),
            json={
                "sample_rate": INPUT_SAMPLE_RATE,
                "channels": 1,
                "audio_format": "pcm",
            },
            timeout=10,
        )
        return _json_or_error(response)

    @app.post("/api/local-mic/chunk")
    def local_mic_chunk(payload: LocalMicChunkPayload) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        response = requests.post(
            urljoin(current["service_url"], "/voice/live/chunk"),
            json={
                "session_id": payload.session_id,
                "audio_base64": payload.audio_base64,
                "is_final": payload.is_final,
            },
            timeout=5,
        )
        return _json_or_error(response)

    @app.get("/api/local-mic/transcript")
    def local_mic_transcript(session_id: str) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        response = requests.get(
            urljoin(current["service_url"], "/voice/live/transcript"),
            params={"session_id": session_id},
            timeout=3,
        )
        return _json_or_error(response)

    @app.post("/api/local-mic/abort")
    def local_mic_abort(payload: LocalMicAbortPayload) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        response = requests.post(
            urljoin(current["service_url"], "/voice/live/abort"),
            json={"session_id": payload.session_id},
            timeout=5,
        )
        return _json_or_error(response)

    @app.post("/api/local-mic/finish-stream")
    def local_mic_finish_stream(payload: LocalMicFinishStreamPayload) -> StreamingResponse:
        current = _snapshot(settings, settings_lock)
        conversation_id = (
            payload.conversation_id or current["conversation_id"] or DEFAULT_CONVERSATION_ID
        ).strip()

        def event_stream():
            response: requests.Response | None = None
            try:
                response = requests.post(
                    urljoin(current["service_url"], "/tools/voice-latency/finish-stream"),
                    json={
                        "session_id": payload.session_id,
                        "conversation_id": conversation_id,
                        "tts_enabled": payload.tts_enabled,
                    },
                    stream=True,
                    timeout=(10, 120),
                )
                for item in _iter_sse_events(response):
                    event = str(item.get("event") or "message")
                    data = item.get("data") or {}
                    if event == "done":
                        behavior_results = _trigger_behaviors_from_text(
                            str(data.get("reply") or ""),
                            behavior_config,
                        )
                        for result in behavior_results:
                            yield _sse_frame(
                                "behavior",
                                _behavior_result_payload(result),
                            )
                    yield _sse_frame(
                        event,
                        data,
                    )
            except HTTPException as exc:
                yield _sse_frame("error", {"message": str(exc.detail)})
            except Exception as exc:
                yield _sse_frame(
                    "error",
                    {"message": str(exc) or exc.__class__.__name__},
                )
            finally:
                if response is not None:
                    response.close()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


def _build_web_only_app() -> FastAPI:
    app = FastAPI()
    settings_lock = threading.Lock()
    settings = _default_settings()
    behavior_config = _load_behavior_config()
    _disable_behavior_module(behavior_config, "action")
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index_page() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    _register_settings_routes(app, settings, settings_lock)
    _register_text_chat_routes(
        app,
        settings,
        settings_lock,
        behavior_config=behavior_config,
    )
    _register_local_mic_routes(
        app,
        settings,
        settings_lock,
        behavior_config=behavior_config,
    )

    @app.get("/api/app-mode")
    def app_mode() -> dict[str, Any]:
        return {"web_only": True}

    @app.get("/api/audio-volume")
    def web_only_audio_volume() -> dict[str, Any]:
        return {
            "speaker": {"volume": None, "available": False},
            "microphone": {"volume": None, "available": False},
        }

    @app.get("/api/emoji-config")
    def get_emoji_config() -> dict[str, Any]:
        return _public_emoji_config(behavior_config)

    @app.get("/api/behavior-config")
    def get_behavior_config() -> dict[str, Any]:
        return _public_behavior_config(behavior_config)

    return app


def run_web_only(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(_build_web_only_app(), host=host, port=port)


TAG_PATTERN = re.compile(r"\[([^:\]\r\n]+):([^\]\r\n]+)\]")


def _default_behavior_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "modules": {
            "emoji": {
                "enabled": True,
                "tag_names": ["emo", "emotion", "表情"],
                "service_url": DEFAULT_EMOJI_SERVICE_URL,
                "request_timeout_seconds": 1.5,
                "method": "GET",
                "endpoint_template": "/{key}",
                "triggers": [
                    "😀",
                    "😄",
                    "😁",
                    "angry",
                    "sad",
                    "scared",
                    "fear",
                    "excited",
                    "idle",
                    "smug",
                    "surprised",
                    "surprise",
                    "😧",
                    "开心",
                    "难过",
                ],
            },
            "action": {
                "enabled": True,
                "tag_names": ["act", "action", "动作"],
                "trigger_mode": "function",
                "config_path": "../../action_call/config.json",
                "library_dir": "../../action_call/library",
                "sound": False,
                "final_home_check": True,
                "home_tolerance_deg": 5.0,
                "reset_duration": 1.5,
                "reset_attempts": 2,
                "triggers": "*",
            },
        },
    }


def _load_behavior_config() -> dict[str, Any]:
    config = _default_behavior_config()
    config_path = _resolve_behavior_config_path()
    try:
        loaded = _load_structured_config(config_path)
        if isinstance(loaded, dict):
            _merge_behavior_config(config, loaded)
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"Failed to load behavior config {config_path}: {exc}")

    enabled_override = os.environ.get("REACHY_DIALOGUE_BEHAVIOR_ENABLED")
    if enabled_override is not None:
        config["enabled"] = _env_flag(enabled_override)
    else:
        config["enabled"] = bool(config.get("enabled", True))

    emoji_enabled = os.environ.get("REACHY_DIALOGUE_EMOJI_ENABLED")
    if emoji_enabled is not None:
        config["modules"]["emoji"]["enabled"] = _env_flag(emoji_enabled)

    emoji_url = (
        os.environ.get("REACHY_DIALOGUE_EMOJI_SERVICE_URL")
        or os.environ.get("REACHY_EMOJI_SERVICE_URL")
    )
    if emoji_url:
        config["modules"]["emoji"]["service_url"] = emoji_url

    _normalize_behavior_config(config, base_dir=config_path.parent)
    return config


def _resolve_behavior_config_path() -> Path:
    explicit = (
        os.environ.get("REACHY_DIALOGUE_BEHAVIOR_CONFIG")
        or os.environ.get("REACHY_DIALOGUE_EMOJI_CONFIG")
    )
    if explicit:
        return Path(explicit).expanduser()
    if DEFAULT_BEHAVIOR_CONFIG_FILE.exists():
        return DEFAULT_BEHAVIOR_CONFIG_FILE
    return DEFAULT_EMOJI_CONFIG_FILE


def _load_structured_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as config_file:
        if config_path.suffix.lower() in {".yaml", ".yml"}:
            if yaml is None:
                raise RuntimeError("PyYAML is required to load YAML config files")
            loaded = yaml.safe_load(config_file) or {}
        else:
            loaded = json.load(config_file)
    if isinstance(loaded, dict):
        return loaded
    return {}


def _merge_behavior_config(config: dict[str, Any], loaded: dict[str, Any]) -> None:
    if "modules" in loaded and isinstance(loaded.get("modules"), dict):
        if "enabled" in loaded:
            config["enabled"] = loaded["enabled"]
        for module_name, module_config in loaded["modules"].items():
            if not isinstance(module_config, dict):
                continue
            current = config["modules"].setdefault(str(module_name), {})
            current.update(module_config)
        return

    # Legacy emoji_config.json support: use signal_map keys as emoji triggers.
    emoji_module = config["modules"]["emoji"]
    if "enabled" in loaded:
        config["enabled"] = loaded["enabled"]
        emoji_module["enabled"] = loaded["enabled"]
    for key in ("service_url", "request_timeout_seconds"):
        if key in loaded:
            emoji_module[key] = loaded[key]
    signal_map = loaded.get("signal_map")
    if isinstance(signal_map, dict):
        emoji_module["triggers"] = list(signal_map.keys())


def _normalize_behavior_config(config: dict[str, Any], *, base_dir: Path) -> None:
    modules = config.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    config["modules"] = modules
    for module_name, module_config in list(modules.items()):
        if not isinstance(module_config, dict):
            modules.pop(module_name)
            continue
        module_config["enabled"] = bool(module_config.get("enabled", True))
        module_config["tag_names"] = _normalize_string_list(
            module_config.get("tag_names")
        )
        if not module_config["tag_names"]:
            module_config["tag_names"] = [str(module_name)]
        module_config["service_url"] = str(
            module_config.get("service_url") or ""
        ).rstrip("/")
        module_config["method"] = str(module_config.get("method") or "GET").upper()
        module_config["trigger_mode"] = str(
            module_config.get("trigger_mode") or "http"
        ).lower()
        module_config["endpoint_template"] = str(
            module_config.get("endpoint_template") or "/{key}"
        )
        module_config["triggers"] = _normalize_triggers(
            module_config.get("triggers")
        )
        try:
            module_config["request_timeout_seconds"] = float(
                module_config.get("request_timeout_seconds", 3.0)
            )
        except (TypeError, ValueError):
            module_config["request_timeout_seconds"] = 3.0
        for key in ("config_path", "library_dir"):
            if key in module_config:
                module_config[key] = str(_resolve_behavior_path(module_config[key], base_dir))


def _resolve_behavior_path(value: Any, base_dir: Path) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_triggers(value: Any) -> str | list[str]:
    if value == "*":
        return "*"
    return _normalize_string_list(value)


def _env_flag(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def _public_behavior_config(config: dict[str, Any]) -> dict[str, Any]:
    public_modules: dict[str, Any] = {}
    for module_name, module_config in (config.get("modules") or {}).items():
        if not isinstance(module_config, dict):
            continue
        public_modules[str(module_name)] = {
            "enabled": bool(module_config.get("enabled")),
            "tag_names": list(module_config.get("tag_names") or []),
            "trigger_mode": module_config.get("trigger_mode"),
            "service_url": module_config.get("service_url"),
            "method": module_config.get("method"),
            "endpoint_template": module_config.get("endpoint_template"),
            "config_path": module_config.get("config_path"),
            "library_dir": module_config.get("library_dir"),
            "triggers": module_config.get("triggers"),
        }
    return {"enabled": bool(config.get("enabled")), "modules": public_modules}


def _public_emoji_config(config: dict[str, Any]) -> dict[str, Any]:
    emoji_module = (config.get("modules") or {}).get("emoji") or {}
    triggers = emoji_module.get("triggers")
    signal_map = {}
    if isinstance(triggers, list):
        signal_map = {trigger: trigger for trigger in triggers}
    return {
        "enabled": bool(config.get("enabled") and emoji_module.get("enabled", True)),
        "service_url": emoji_module.get("service_url"),
        "signal_map": signal_map,
        "available_emotions": [],
        "tag_names": list(emoji_module.get("tag_names") or []),
        "triggers": triggers,
    }


def _trigger_behaviors_from_text(
    text: str,
    config: dict[str, Any] | None,
) -> list[BehaviorTriggerResult]:
    if not config or not config.get("enabled", True):
        return []

    return [
        _trigger_behavior_tag(tag, config)
        for tag in _extract_behavior_tags(text, config)
    ]


def _extract_behavior_tags(
    text: str,
    config: dict[str, Any] | None,
) -> list[BehaviorTag]:
    if not text or not config:
        return []
    tag_to_module: dict[str, str] = {}
    modules = config.get("modules") or {}
    for module_name, module_config in modules.items():
        if not isinstance(module_config, dict) or not module_config.get("enabled", True):
            continue
        for tag_name in module_config.get("tag_names") or []:
            tag_to_module.setdefault(str(tag_name).casefold(), str(module_name))

    tags: list[BehaviorTag] = []
    for match in TAG_PATTERN.finditer(text):
        tag_name = match.group(1).strip()
        key = match.group(2).strip()
        if not tag_name or not key:
            continue
        module = tag_to_module.get(tag_name.casefold())
        if module is None:
            continue
        tags.append(
            BehaviorTag(
                module=module,
                tag_name=tag_name,
                key=key,
                raw=match.group(0),
            )
        )
    return tags


def _trigger_behavior_tag(
    tag: BehaviorTag,
    config: dict[str, Any],
) -> BehaviorTriggerResult:
    module_config = (config.get("modules") or {}).get(tag.module)
    if not isinstance(module_config, dict) or not module_config.get("enabled", True):
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            error="Module disabled",
        )
    if not _trigger_allowed(tag.key, module_config.get("triggers")):
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            error="Trigger key not configured",
        )

    if module_config.get("trigger_mode") == "function":
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            triggered=True,
            ok=True,
        )

    service_url = str(module_config.get("service_url") or "").rstrip("/")
    if not service_url:
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            error="Missing service_url",
        )

    endpoint = _render_endpoint(module_config, tag)
    url = _join_service_url(service_url, endpoint)
    method = str(module_config.get("method") or "GET").upper()
    timeout = float(module_config.get("request_timeout_seconds") or 3.0)
    try:
        if method == "POST":
            response = requests.post(
                url,
                json=_render_json_body(module_config, tag),
                timeout=timeout,
            )
        else:
            response = requests.get(url, timeout=timeout)
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            url=url,
            triggered=True,
            ok=response.ok,
            status_code=response.status_code,
            error=None if response.ok else response.text[:300],
        )
    except requests.RequestException as exc:
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            url=url,
            triggered=True,
            ok=False,
            error=str(exc),
        )


def _trigger_allowed(key: str, triggers: Any) -> bool:
    if triggers == "*":
        return True
    if isinstance(triggers, list):
        return key in triggers
    return False


def _render_endpoint(module_config: dict[str, Any], tag: BehaviorTag) -> str:
    template = str(module_config.get("endpoint_template") or "/{key}")
    return _render_template(template, tag, quote_key=True)


def _render_json_body(module_config: dict[str, Any], tag: BehaviorTag) -> Any:
    body = module_config.get("json_body")
    if body is None:
        body = {"key": "{key}"}
    return _render_template(body, tag, quote_key=False)


def _render_template(value: Any, tag: BehaviorTag, *, quote_key: bool) -> Any:
    replacements = {
        "module": tag.module,
        "tag": tag.tag_name,
        "key": quote(tag.key, safe="") if quote_key else tag.key,
        "raw": quote(tag.raw, safe="") if quote_key else tag.raw,
    }
    if isinstance(value, str):
        rendered = value
        for name, replacement in replacements.items():
            rendered = rendered.replace("{" + name + "}", replacement)
        return rendered
    if isinstance(value, list):
        return [_render_template(item, tag, quote_key=quote_key) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _render_template(item, tag, quote_key=quote_key)
            for key, item in value.items()
        }
    return value


def _join_service_url(service_url: str, endpoint: str) -> str:
    if not endpoint:
        endpoint = "/"
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return service_url.rstrip("/") + endpoint


def _first_module_result(
    results: list[BehaviorTriggerResult],
    module: str,
) -> BehaviorTriggerResult | None:
    for result in results:
        if result.module == module:
            return result
    return None


def _first_ok_module_key(
    results: list[BehaviorTriggerResult],
    module: str,
) -> str | None:
    result = _first_module_result(results, module)
    if result is None or not result.ok:
        return None
    return result.key


def _module_config(config: dict[str, Any], module: str) -> dict[str, Any] | None:
    module_config = (config.get("modules") or {}).get(module)
    if isinstance(module_config, dict):
        return dict(module_config)
    return None


def _disable_behavior_module(config: dict[str, Any], module: str) -> None:
    module_config = (config.get("modules") or {}).get(module)
    if isinstance(module_config, dict):
        module_config["enabled"] = False


def _behavior_result_payload(result: BehaviorTriggerResult) -> dict[str, Any]:
    return {
        "matched": result.matched,
        "module": result.module,
        "tag": result.tag_name,
        "key": result.key,
        "url": result.url,
        "triggered": result.triggered,
        "ok": result.ok,
        "status_code": result.status_code,
        "error": result.error,
    }


def _emoji_result_payload(result: BehaviorTriggerResult) -> dict[str, Any]:
    return {
        "matched": result.matched,
        "signal": result.key,
        "emotion": result.key,
        "url": result.url,
        "ok": result.ok,
        "status_code": result.status_code,
        "error": result.error,
    }


def _normalize_service_url(value: str) -> str:
    value = value.strip() or DEFAULT_SERVICE_URL
    return value.rstrip("/") + "/"


def _audio_level_from_rms(rms: float) -> float:
    return float(np.clip(rms / 0.08, 0.0, 1.0))


def _json_or_error(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = {"error": {"message": response.text}}
    if not response.ok:
        message = data.get("error", {}).get("message", response.text)
        raise HTTPException(status_code=response.status_code, detail=message)
    return data


def _post_text_chat(
    *,
    service_url: str,
    conversation_id: str,
    text: str,
    tts_enabled: bool,
) -> dict[str, Any]:
    request_variants = [
        {
            "conversation_id": conversation_id,
            "message": text,
            "tts_enabled": tts_enabled,
        },
        {
            "conversation_id": conversation_id,
            "text": text,
            "tts_enabled": tts_enabled,
        },
    ]
    last_response: requests.Response | None = None
    for index, body in enumerate(request_variants):
        response = requests.post(
            urljoin(service_url, "/chat"),
            json=body,
            timeout=90,
        )
        if response.ok:
            return _json_or_error(response)
        last_response = response
        if response.status_code not in {400, 422} or index == len(request_variants) - 1:
            break
    assert last_response is not None
    return _json_or_error(last_response)


def _reply_text_from_payload(data: dict[str, Any]) -> str:
    for key in ("reply", "response", "answer", "text"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _daemon_volume_request(
    reachy_mini: ReachyMini,
    method: str,
    endpoint: str,
    *,
    volume: int | None = None,
) -> dict[str, Any]:
    daemon_url = getattr(reachy_mini, "_daemon_http_url", "").rstrip("/")
    if not daemon_url:
        daemon_url = f"http://{reachy_mini.host}:{reachy_mini.port}"
    body = None
    if volume is not None:
        body = {"volume": max(0, min(100, int(volume)))}
    try:
        response = requests.request(
            method,
            daemon_url + endpoint,
            json=body,
            timeout=5,
        )
        return _json_or_error(response)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"音量接口不可用：{exc}",
        ) from exc


def _iter_sse_events(response: requests.Response):
    if not response.ok:
        _json_or_error(response)

    event = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                yield {
                    "event": event,
                    "data": _decode_sse_json("\n".join(data_lines)),
                }
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())

    if data_lines:
        yield {
            "event": event,
            "data": _decode_sse_json("\n".join(data_lines)),
        }


def _decode_sse_json(payload: str) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"text": payload}
    if isinstance(data, dict):
        return data
    return {"value": data}


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _handle_robot_job(reachy_mini: ReachyMini, job: RobotJob) -> None:
    wav_path = None
    playback_seconds = 0.0
    try:
        if job.audio_base64:
            audio_bytes = base64.b64decode(job.audio_base64)
            playback_seconds = len(audio_bytes) / (2.0 * job.audio_sample_rate)
            wav_path = _write_pcm_wav(audio_bytes, job.audio_sample_rate)
        started_at = time.monotonic()
        if wav_path is not None:
            reachy_mini.media.play_sound(wav_path)
        if job.action_signal:
            _play_action_signal(reachy_mini, job.action_signal, job.action_config)
        if playback_seconds > 0:
            elapsed = time.monotonic() - started_at
            time.sleep(max(0.3, playback_seconds - elapsed + 0.3))
    except Exception as exc:
        print(f"Robot response failed: {exc}")
    finally:
        if wav_path is not None:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


def _play_action_signal(
    reachy_mini: ReachyMini,
    signal: str,
    action_config: dict[str, Any] | None,
) -> None:
    module = _load_action_call_module()
    config = action_config or {}
    module.play_signal_on_reachy(
        reachy_mini,
        signal,
        config_path=Path(
            config.get("config_path") or REPO_ROOT / "action_call" / "config.json"
        ),
        library_dir=Path(
            config.get("library_dir") or REPO_ROOT / "action_call" / "library"
        ),
        sound=bool(config.get("sound", False)),
        final_home_check=bool(config.get("final_home_check", True)),
        home_tolerance_deg=float(config.get("home_tolerance_deg", 5.0)),
        reset_duration=float(config.get("reset_duration", 1.5)),
        reset_attempts=int(config.get("reset_attempts", 2)),
    )


def _load_action_call_module() -> Any:
    module_path = REPO_ROOT / "action_call" / "play_emotion_action.py"
    spec = importlib.util.spec_from_file_location(
        "reachy_dialogue_action_call",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load action_call module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_pcm_wav(audio_bytes: bytes, sample_rate: int) -> str:
    with tempfile.NamedTemporaryFile(
        prefix="reachy_dialogue_",
        suffix=".wav",
        delete=False,
    ) as temp_file:
        path = temp_file.name
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_bytes)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Reachy voice dialogue app.")
    parser.add_argument(
        "--robot-host",
        default=os.environ.get("REACHY_ROBOT_HOST"),
        help=(
            "Reachy daemon hostname or IP. Use the robot IP for Wireless, "
            "or 127.0.0.1 for a local Lite/sim daemon."
        ),
    )
    parser.add_argument(
        "--robot-port",
        type=int,
        default=int(os.environ.get("REACHY_ROBOT_PORT", DEFAULT_ROBOT_PORT)),
        help="Reachy daemon HTTP/WebSocket port.",
    )
    parser.add_argument(
        "--spawn-daemon",
        action="store_true",
        default=os.environ.get("REACHY_SPAWN_DAEMON", "").lower()
        in {"1", "true", "yes"},
        help="Start reachy-mini-daemon before connecting.",
    )
    parser.add_argument(
        "--use-sim",
        action="store_true",
        default=os.environ.get("REACHY_USE_SIM", "").lower() in {"1", "true", "yes"},
        help="Use the MuJoCo simulated daemon when --spawn-daemon is set.",
    )
    parser.add_argument(
        "--mockup-sim",
        action="store_true",
        default=os.environ.get("REACHY_MOCKUP_SIM", "").lower()
        in {"1", "true", "yes"},
        help="Start a lightweight mockup daemon that does not require MuJoCo.",
    )
    parser.add_argument(
        "--web-only",
        action="store_true",
        default=os.environ.get("REACHY_DIALOGUE_WEB_ONLY", "").lower()
        in {"1", "true", "yes"},
        help="Serve the browser-only text and local-microphone pages; do not connect to Reachy.",
    )
    parser.add_argument(
        "--web-host",
        default=os.environ.get("REACHY_DIALOGUE_WEB_HOST", "127.0.0.1"),
        help="Host for --web-only mode.",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=int(os.environ.get("REACHY_DIALOGUE_WEB_PORT", "8042")),
        help="Port for --web-only mode.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.web_only:
        run_web_only(args.web_host, args.web_port)
        return

    if args.mockup_sim:
        _spawn_mockup_daemon()
        args.spawn_daemon = False
        args.use_sim = False
        args.robot_host = args.robot_host or "127.0.0.1"

    robot_host = args.robot_host
    if robot_host is None:
        if args.spawn_daemon:
            robot_host = "127.0.0.1"
        else:
            print(
                "Reachy Mini daemon host is required.\n\n"
                "Wireless:\n"
                "  python -m reachy_dialogue_app.main --robot-host <robot-ip>\n\n"
                "Lite / local daemon:\n"
                "  python -m reachy_dialogue_app.main --robot-host 127.0.0.1 --spawn-daemon\n\n"
                "Simulation:\n"
                "  python -m reachy_dialogue_app.main --mockup-sim\n",
                file=sys.stderr,
            )
            raise SystemExit(2)

    app = ReachyDialogueApp()
    try:
        app.wrapped_run(
            host=robot_host,
            port=args.robot_port,
            spawn_daemon=args.spawn_daemon,
            use_sim=args.use_sim,
        )
    except KeyboardInterrupt:
        app.stop()


def _spawn_mockup_daemon() -> None:
    subprocess.Popen(
        [
            "reachy-mini-daemon",
            "--mockup-sim",
            "--no-media",
            "--headless",
            "--localhost-only",
        ],
        start_new_session=True,
    )
    deadline = time.time() + 10.0
    last_error = ""
    while time.time() < deadline:
        try:
            response = requests.get(
                "http://127.0.0.1:8000/api/daemon/status",
                timeout=1,
            )
            if response.ok and response.json().get("state") == "running":
                return
            last_error = response.text
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"Mockup daemon did not become ready: {last_error}")


if __name__ == "__main__":
    main()
