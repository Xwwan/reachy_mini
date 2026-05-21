from __future__ import annotations

import base64
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal
from urllib.parse import urljoin

import numpy as np
import requests

from .vad import (
    SILERO_SAMPLE_RATE,
    SileroVad,
    UtteranceSegmenter,
    VadConfig,
    float_to_pcm16_base64,
    normalize_audio_sample,
    pcm16_bytes_to_float,
)


AutoVoiceMode = Literal["local", "robot"]
RobotAudioSource = Callable[[], tuple[np.ndarray | None, int]]
StreamHook = Callable[[str, dict[str, Any]], tuple[list[tuple[str, dict[str, Any]]], threading.Event | None]]
StreamHookFactory = Callable[[str], StreamHook]


@dataclass
class AutoVoiceConfig:
    vad: VadConfig
    input_gain: float = 1.0
    local_chunk_queue_size: int = 80
    robot_poll_seconds: float = 0.01
    transcript_poll_seconds: float = 0.3
    service_timeout_seconds: int = 120


@dataclass
class AutoVoiceSnapshot:
    session_id: str
    mode: AutoVoiceMode
    state: str
    conversation_id: str
    tts_enabled: bool
    utterance_count: int
    last_error: str | None
    speech_probability: float
    rms: float
    peak: float


class AutoVoiceSession:
    def __init__(
        self,
        *,
        session_id: str,
        mode: AutoVoiceMode,
        service_url: str,
        conversation_id: str,
        tts_enabled: bool,
        model_path: Path,
        config: AutoVoiceConfig,
        robot_audio_source: RobotAudioSource | None = None,
        stream_hook: StreamHook | None = None,
    ) -> None:
        self.session_id = session_id
        self.mode = mode
        self.service_url = service_url.rstrip("/") + "/"
        self.conversation_id = conversation_id
        self.tts_enabled = tts_enabled
        self.config = config
        self.robot_audio_source = robot_audio_source
        self.stream_hook = stream_hook
        self.vad = SileroVad(model_path, sample_rate=config.vad.sample_rate)
        self.segmenter = UtteranceSegmenter(self.vad, config.vad)
        self.input_queue: queue.Queue[np.ndarray | None] = queue.Queue(
            maxsize=config.local_chunk_queue_size
        )
        self.events: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.state = "starting"
        self.utterance_count = 0
        self.last_error: str | None = None
        self.last_emit = 0.0
        self.last_drop_emit = 0.0
        self.last_transcript_poll = 0.0
        self.dropped_input_chunks = 0
        self.active_live_session_id: str | None = None
        self.active_utterance_id: str | None = None
        self.last_transcript = ""
        self.thread.start()

    def submit_pcm16_base64(self, audio_base64: str, sample_rate: int) -> bool:
        if self.mode != "local":
            raise RuntimeError("Only local auto voice sessions accept browser chunks.")
        with self.lock:
            state = self.state
        if state not in {"listening", "user_speaking"}:
            self._drop_local_input_chunk(state)
            return False
        samples = pcm16_bytes_to_float(base64.b64decode(audio_base64))
        normalized = normalize_audio_sample(samples, sample_rate, self.config.vad.sample_rate)
        normalized = self._apply_input_gain(normalized)
        try:
            self.input_queue.put_nowait(normalized)
            return True
        except queue.Full:
            self._drop_local_input_chunk("queue_full")
            self._emit(
                "warning",
                {"message": "auto voice input queue is full; dropping local audio"},
            )
            return False

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.input_queue.put_nowait(None)
        except queue.Full:
            pass
        self.events.put(None)

    def snapshot(self) -> AutoVoiceSnapshot:
        with self.lock:
            return AutoVoiceSnapshot(
                session_id=self.session_id,
                mode=self.mode,
                state=self.state,
                conversation_id=self.conversation_id,
                tts_enabled=self.tts_enabled,
                utterance_count=self.utterance_count,
                last_error=self.last_error,
                speech_probability=self.segmenter.last_probability,
                rms=self.segmenter.last_rms,
                peak=self.segmenter.last_peak,
            )

    def event_stream(self) -> Iterable[tuple[str, dict[str, Any]]]:
        self._emit("snapshot", asdict(self.snapshot()))
        while not self.stop_event.is_set():
            item = self.events.get()
            if item is None:
                break
            yield item

    def _run(self) -> None:
        self._set_state("listening")
        try:
            while not self.stop_event.is_set():
                samples = self._read_samples()
                if samples is None:
                    continue
                if self.state not in {"listening", "user_speaking"}:
                    continue
                if self.active_live_session_id is not None:
                    self._send_audio_to_live_session(
                        self.active_live_session_id,
                        samples,
                        is_final=False,
                    )
                    self._emit_live_transcript_if_due()
                for vad_event in self.segmenter.feed(samples):
                    payload = {
                        "session_id": self.session_id,
                        "speech_probability": vad_event.speech_probability,
                        "rms": vad_event.rms,
                        "peak": vad_event.peak,
                        "duration_seconds": vad_event.duration_seconds,
                    }
                    if vad_event.event == "speech_start":
                        self._set_state("user_speaking")
                        self._start_streaming_transcription(vad_event.audio)
                        self._emit("speech_start", payload)
                    elif vad_event.event.startswith("speech_end"):
                        self._emit("speech_end", payload)
                        self._finish_streaming_transcription()
                    elif vad_event.event == "speech_cancelled":
                        self._emit("speech_cancelled", payload)
                        self._abort_live_session()
                        self._set_state("listening")
                self._emit_level_if_due()
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc) or exc.__class__.__name__
            self._set_state("error")
            self._emit("error", {"message": self.last_error})
        finally:
            self._set_state("stopped")

    def _read_samples(self) -> np.ndarray | None:
        if self.mode == "robot":
            if self.robot_audio_source is None:
                raise RuntimeError("Robot auto voice session has no audio source.")
            sample, sample_rate = self.robot_audio_source()
            if sample is None:
                time.sleep(self.config.robot_poll_seconds)
                return None
            normalized = normalize_audio_sample(
                sample,
                sample_rate,
                self.config.vad.sample_rate,
            )
            return self._apply_input_gain(normalized)

        try:
            item = self.input_queue.get(timeout=0.2)
        except queue.Empty:
            return None
        if item is None:
            self.stop_event.set()
            return None
        return item

    def _apply_input_gain(self, samples: np.ndarray) -> np.ndarray:
        gain = max(0.0, float(self.config.input_gain))
        if gain == 1.0:
            return samples
        return np.clip(samples * gain, -1.0, 1.0).astype(np.float32, copy=False)

    def _start_streaming_transcription(self, initial_audio: np.ndarray | None) -> None:
        self.utterance_count += 1
        self.last_transcript = ""
        self.last_transcript_poll = 0.0
        utterance_id = f"utt_{self.utterance_count}"
        live_session_id = self._start_live_session()
        self.active_live_session_id = live_session_id
        self.active_utterance_id = utterance_id
        self._emit(
            "utterance",
            {
                "session_id": self.session_id,
                "utterance_id": utterance_id,
                "live_session_id": live_session_id,
            },
        )
        if initial_audio is not None and initial_audio.size:
            self._send_audio_to_live_session(
                live_session_id,
                initial_audio,
                is_final=False,
            )
            self._emit_live_transcript(force=True)

    def _finish_streaming_transcription(self) -> None:
        live_session_id = self.active_live_session_id
        if live_session_id is None:
            self._set_state("listening")
            return
        self._emit_live_transcript(force=True)
        self._process_live_session(live_session_id)

    def _process_live_session(self, live_session_id: str) -> None:
        self._set_state("transcribing")

        response: requests.Response | None = None
        output_audio_seconds = 0.0
        try:
            self._set_state("assistant_streaming")
            response = requests.post(
                urljoin(self.service_url, "/voice/live/finish-stream"),
                json={
                    "session_id": live_session_id,
                    "conversation_id": self.conversation_id,
                    "tts_enabled": self.tts_enabled,
                },
                stream=True,
                timeout=(10, self.config.service_timeout_seconds),
            )
            for event, data in iter_sse_events(response):
                if event == "audio":
                    output_audio_seconds += audio_duration_from_payload(data)
                barrier: threading.Event | None = None
                extras: list[tuple[str, dict[str, Any]]] = []
                if self.stream_hook is not None and event in {"audio", "done"}:
                    extras, barrier = self.stream_hook(event, data)
                for extra_event, extra_payload in extras:
                    self._emit(extra_event, extra_payload)
                self._emit(event, data)
                if event == "done":
                    self._set_state("speaking" if output_audio_seconds > 0 else "cooldown")
                    if barrier is not None:
                        barrier.wait(timeout=self.config.service_timeout_seconds)
                    elif output_audio_seconds > 0:
                        time.sleep(max(0.0, output_audio_seconds + 0.2))
                    self._emit("playback_done", {"ok": True, "session_id": self.session_id})
                    self._cooldown_then_listen()
                    return
                if event == "error":
                    self._set_state("listening")
                    return
            self._cooldown_then_listen()
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc) or exc.__class__.__name__
            self._emit("error", {"message": self.last_error})
            self._set_state("listening")
        finally:
            self.active_live_session_id = None
            self.active_utterance_id = None
            self.last_transcript = ""
            if response is not None:
                response.close()

    def _start_live_session(self) -> str:
        response = requests.post(
            urljoin(self.service_url, "/voice/live/start"),
            json={
                "sample_rate": self.config.vad.sample_rate,
                "channels": 1,
                "audio_format": "pcm",
            },
            timeout=10,
        )
        data = json_or_error(response)
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("voice/live/start did not return a session_id")
        return session_id

    def _send_audio_to_live_session(
        self,
        live_session_id: str,
        audio: np.ndarray,
        *,
        is_final: bool,
    ) -> None:
        pcm_base64 = float_to_pcm16_base64(audio)
        raw = base64.b64decode(pcm_base64)
        chunk_size = 5120
        for offset in range(0, len(raw), chunk_size):
            chunk = raw[offset : offset + chunk_size]
            chunk_is_final = is_final and offset + chunk_size >= len(raw)
            response = requests.post(
                urljoin(self.service_url, "/voice/live/chunk"),
                json={
                    "session_id": live_session_id,
                    "audio_base64": base64.b64encode(chunk).decode("ascii"),
                    "is_final": chunk_is_final,
                },
                timeout=10,
            )
            json_or_error(response)

    def _emit_live_transcript_if_due(self) -> None:
        now = time.monotonic()
        if now - self.last_transcript_poll < self.config.transcript_poll_seconds:
            return
        self.last_transcript_poll = now
        self._emit_live_transcript()

    def _emit_live_transcript(self, *, force: bool = False) -> None:
        live_session_id = self.active_live_session_id
        if live_session_id is None:
            return
        try:
            response = requests.get(
                urljoin(self.service_url, "/voice/live/transcript"),
                params={"session_id": live_session_id},
                timeout=3,
            )
            data = json_or_error(response)
        except Exception as exc:
            if force:
                self._emit("warning", {"message": f"读取实时字幕失败：{exc}"})
            return
        transcript = str(data.get("transcript") or data.get("text") or "")
        if not force and transcript == self.last_transcript:
            return
        self.last_transcript = transcript
        self._emit(
            "transcript",
            {
                "session_id": self.session_id,
                "live_session_id": live_session_id,
                "utterance_id": self.active_utterance_id,
                "transcript": transcript,
                "is_final": bool(data.get("is_final", False)),
            },
        )

    def _abort_live_session(self) -> None:
        live_session_id = self.active_live_session_id
        self.active_live_session_id = None
        self.active_utterance_id = None
        self.last_transcript = ""
        if live_session_id is None:
            return
        try:
            requests.post(
                urljoin(self.service_url, "/voice/live/abort"),
                json={"session_id": live_session_id},
                timeout=5,
            )
        except Exception:
            pass

    def _cooldown_then_listen(self) -> None:
        self._set_state("cooldown")
        time.sleep(max(0.0, self.config.vad.cooldown_ms / 1000.0))
        self._drain_local_input_queue()
        self.segmenter.reset()
        self._set_state("listening")

    def _drop_local_input_chunk(self, reason: str) -> None:
        self.dropped_input_chunks += 1
        now = time.monotonic()
        if now - self.last_drop_emit < 1.0:
            return
        self.last_drop_emit = now
        self._emit(
            "input_dropped",
            {
                "session_id": self.session_id,
                "reason": reason,
                "dropped_input_chunks": self.dropped_input_chunks,
            },
        )

    def _drain_local_input_queue(self) -> None:
        if self.mode != "local":
            return
        drained = 0
        while True:
            try:
                self.input_queue.get_nowait()
                drained += 1
            except queue.Empty:
                break
        if drained:
            self._emit(
                "input_drained",
                {
                    "session_id": self.session_id,
                    "drained_input_chunks": drained,
                },
            )

    def _emit_level_if_due(self) -> None:
        now = time.monotonic()
        if now - self.last_emit < 0.2:
            return
        self.last_emit = now
        self._emit(
            "level",
            {
                "session_id": self.session_id,
                "speech_probability": self.segmenter.last_probability,
                "rms": self.segmenter.last_rms,
                "peak": self.segmenter.last_peak,
                "state": self.state,
            },
        )

    def _set_state(self, state: str) -> None:
        with self.lock:
            self.state = state
        self._emit("state", {"session_id": self.session_id, "state": state})

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        self.events.put((event, payload))


class AutoVoiceManager:
    def __init__(
        self,
        *,
        model_path: Path,
        config: AutoVoiceConfig,
        service_url_getter: Callable[[], str],
        robot_audio_source: RobotAudioSource | None = None,
        stream_hook_factory: StreamHookFactory | None = None,
    ) -> None:
        self.model_path = model_path
        self.config = config
        self.service_url_getter = service_url_getter
        self.robot_audio_source = robot_audio_source
        self.stream_hook_factory = stream_hook_factory
        self.lock = threading.Lock()
        self.sessions: dict[str, AutoVoiceSession] = {}

    def start(
        self,
        *,
        mode: AutoVoiceMode,
        conversation_id: str,
        tts_enabled: bool,
    ) -> AutoVoiceSession:
        session_id = f"auto_{uuid.uuid4().hex}"
        session = AutoVoiceSession(
            session_id=session_id,
            mode=mode,
            service_url=self.service_url_getter(),
            conversation_id=conversation_id,
            tts_enabled=tts_enabled,
            model_path=self.model_path,
            config=self.config,
            robot_audio_source=self.robot_audio_source,
            stream_hook=(
                self.stream_hook_factory(session_id)
                if self.stream_hook_factory is not None
                else None
            ),
        )
        with self.lock:
            self.sessions[session_id] = session
        return session

    def get(self, session_id: str) -> AutoVoiceSession:
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def stop(self, session_id: str) -> None:
        session = self.get(session_id)
        session.stop()
        with self.lock:
            self.sessions.pop(session_id, None)

    def snapshot(self, session_id: str) -> AutoVoiceSnapshot:
        return self.get(session_id).snapshot()


def audio_duration_from_payload(data: dict[str, Any]) -> float:
    audio_base64 = data.get("audio_base64")
    if not isinstance(audio_base64, str) or not audio_base64:
        return 0.0
    sample_rate = int(data.get("sample_rate") or data.get("audio_sample_rate") or 24000)
    try:
        audio_bytes = base64.b64decode(audio_base64)
    except Exception:
        return 0.0
    return len(audio_bytes) / max(1.0, 2.0 * float(sample_rate))


def iter_sse_events(response: requests.Response):
    if not response.ok:
        json_or_error(response)
    event = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                yield event, decode_sse_json("\n".join(data_lines))
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
        yield event, decode_sse_json("\n".join(data_lines))


def decode_sse_json(payload: str) -> dict[str, Any]:
    import json

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"text": payload}
    if isinstance(data, dict):
        return data
    return {"value": data}


def json_or_error(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        if response.ok:
            return {}
        raise RuntimeError(response.text or response.reason) from exc
    if not response.ok:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise RuntimeError(str(detail or data or response.reason))
    if isinstance(data, dict):
        return data
    return {"value": data}
