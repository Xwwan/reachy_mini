import argparse
import base64
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import numpy as np
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_mini.utils import create_head_pose


DEFAULT_SERVICE_URL = "http://127.0.0.1:12312"
DEFAULT_CONVERSATION_ID = "reachy-mini-voice"
DEFAULT_GESTURE = "shake_head"
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
DEFAULT_ROBOT_PORT = 8000
MIN_RECORDING_DURATION_SECONDS = 0.3
MIN_RECORDING_RMS = 0.0002
LIVE_CHUNK_BYTES = 5120


@dataclass
class RobotJob:
    gesture: str
    audio_base64: str | None = None
    audio_sample_rate: int = OUTPUT_SAMPLE_RATE
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


class SettingsPayload(BaseModel):
    service_url: str | None = None
    conversation_id: str | None = None
    gesture: str | None = None
    tts_sample_rate: int | None = None


class VoiceChatPayload(BaseModel):
    audio_base64: str
    audio_format: str = "pcm"
    conversation_id: str | None = None
    tts_enabled: bool = True


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
    ) -> None:
        self.service_url = _normalize_service_url(service_url)
        self.session_id = session_id
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
    def start(cls, service_url: str) -> "LiveVoiceSession":
        normalized = _normalize_service_url(service_url)
        response = requests.post(
            urljoin(normalized, "/voice/live/start"),
            json={
                "sample_rate": INPUT_SAMPLE_RATE,
                "channels": 1,
                "audio_format": "pcm",
            },
            timeout=10,
        )
        data = _json_or_error(response)
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("实时语音服务没有返回有效 session_id。")
        return cls(service_url=normalized, session_id=session_id)

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
            urljoin(self.service_url, "/tools/voice-latency/finish-stream"),
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
            data = self._finish_json(conversation_id=conversation_id, tts_enabled=True)
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
            if self.reachy_mini.media.get_input_audio_samplerate() < 0:
                raise RuntimeError(
                    "机器人音频系统未初始化。请使用启用了媒体功能的真实 "
                    "Reachy daemon 启动 app；--mockup-sim 会使用 --no-media，"
                    "不能录制机器人麦克风音频。"
                )
            self.samples = []
            self.captured_frames = 0
            self.empty_sample_count = 0
            self.latest_level = AudioLevel(
                is_recording=True,
                duration_seconds=0.0,
                rms=0.0,
                peak=0.0,
                level=0.0,
            )
            self.stop_event.clear()
            self.live_session = LiveVoiceSession.start(service_url)
            self.live_transcript = LiveTranscript(
                text="",
                is_final=False,
                error=None,
            )
            self.final_response = None
            self.reachy_mini.media.start_recording()
            self.is_recording = True
            self.started_at = time.time()
            self.thread = threading.Thread(target=self._record_loop, daemon=True)
            self.thread.start()

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
        duration_seconds = float(pcm.shape[0] / INPUT_SAMPLE_RATE)
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
            sample_rate=INPUT_SAMPLE_RATE,
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
            else:
                with self.lock:
                    self.empty_sample_count += 1
            time.sleep(0.02)


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
            self.reachy_mini.media.start_recording()
            self.is_recording = True
            self.started_at = time.time()
            self.thread = threading.Thread(target=self._record_loop, daemon=True)
            self.thread.start()

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
            else:
                with self.lock:
                    self.empty_sample_count += 1
            time.sleep(0.02)


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
            "gesture": os.environ.get("REACHY_DIALOGUE_GESTURE", DEFAULT_GESTURE),
            "tts_sample_rate": int(
                os.environ.get("REACHY_DIALOGUE_TTS_SAMPLE_RATE", OUTPUT_SAMPLE_RATE)
            ),
        }
        jobs: queue.Queue[RobotJob] = queue.Queue()
        recorder = RobotMicRecorder(reachy_mini)
        playback_tester = RobotMicPlaybackTester(reachy_mini)
        _register_local_mic_routes(self.settings_app, settings, settings_lock)

        @self.settings_app.get("/api/settings")
        def get_settings() -> dict[str, Any]:
            with settings_lock:
                return dict(settings)

        @self.settings_app.post("/api/settings")
        def update_settings(payload: SettingsPayload) -> dict[str, Any]:
            with settings_lock:
                if payload.service_url is not None:
                    settings["service_url"] = _normalize_service_url(
                        payload.service_url
                    )
                if payload.conversation_id is not None:
                    settings["conversation_id"] = payload.conversation_id.strip()
                if payload.gesture is not None:
                    settings["gesture"] = payload.gesture
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
                jobs.put(
                    RobotJob(
                        gesture=current["gesture"],
                        audio_base64=data.get("audio_base64"),
                        audio_sample_rate=int(current["tts_sample_rate"]),
                    )
                )
            return data

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
                        gesture=current["gesture"],
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
                            audio_base64 = data.get("audio_base64")
                            if not audio_base64 and audio_chunks:
                                audio_base64 = base64.b64encode(
                                    b"".join(audio_chunks)
                                ).decode("ascii")
                            playback_done = threading.Event()
                            jobs.put(
                                RobotJob(
                                    gesture=current["gesture"],
                                    audio_base64=audio_base64,
                                    audio_sample_rate=audio_sample_rate,
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
                    gesture="none",
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
        "gesture": os.environ.get("REACHY_DIALOGUE_GESTURE", DEFAULT_GESTURE),
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
            if payload.gesture is not None:
                settings["gesture"] = payload.gesture
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


def _register_local_mic_routes(
    app: FastAPI,
    settings: dict[str, Any],
    settings_lock: threading.Lock,
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
                    yield _sse_frame(
                        str(item.get("event") or "message"),
                        item.get("data") or {},
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
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def local_mic_page() -> FileResponse:
        return FileResponse(static_dir / "local-mic-test.html")

    _register_settings_routes(app, settings, settings_lock)
    _register_local_mic_routes(app, settings, settings_lock)
    return app


def run_web_only(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(_build_web_only_app(), host=host, port=port)


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
        _run_gesture(reachy_mini, job.gesture)
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


def _run_gesture(reachy_mini: ReachyMini, gesture: str) -> None:
    if gesture == "none":
        return
    if gesture == "antenna_wave":
        _antenna_wave(reachy_mini)
        return
    _shake_head(reachy_mini)


def _shake_head(reachy_mini: ReachyMini) -> None:
    poses = [
        create_head_pose(yaw=-18, degrees=True),
        create_head_pose(yaw=18, degrees=True),
        create_head_pose(yaw=-14, degrees=True),
        create_head_pose(yaw=0, degrees=True),
    ]
    for pose in poses:
        reachy_mini.goto_target(head=pose, duration=0.35)
        time.sleep(0.35)


def _antenna_wave(reachy_mini: ReachyMini) -> None:
    for value in (25, -25, 20, 0):
        reachy_mini.goto_target(
            antennas=np.deg2rad(np.array([value, -value])),
            duration=0.25,
            body_yaw=None,
        )
        time.sleep(0.25)


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
        help="Only serve the browser local-microphone test page; do not connect to Reachy.",
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
