from __future__ import annotations

import base64
import queue
import threading
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ..interaction.client import InteractionApiClient
from ..vad import (
    SileroVad,
    UtteranceSegmenter,
    float_to_pcm16_base64,
    normalize_audio_sample,
    pcm16_bytes_to_float,
)
from .sse import audio_duration_from_payload
from .types import (
    AutoVoiceConfig,
    AutoVoiceGateState,
    AutoVoiceMode,
    AutoVoiceSnapshot,
    RobotAudioSource,
    StreamHook,
)


class AutoVoiceSession:
    def __init__(
        self,
        *,
        session_id: str,
        mode: AutoVoiceMode,
        service_url: str,
        conversation_id: str,
        interaction_session_id: str,
        workflow: str,
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
        self.interaction_session_id = interaction_session_id
        self.workflow = "onboarding" if workflow == "onboarding" else "chat"
        self.tts_enabled = tts_enabled
        self.interaction_client = InteractionApiClient(self.service_url)
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
        self.gate_state: AutoVoiceGateState = (
            "waiting_wake" if self.config.wake_gate.enabled else "awake"
        )
        self.last_awake_activity = time.monotonic()
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
                gate_state=self.gate_state,
                wake_gate_enabled=self.config.wake_gate.enabled,
                last_error=self.last_error,
                speech_probability=self.segmenter.last_probability,
                rms=self.segmenter.last_rms,
                peak=self.segmenter.last_peak,
            )

    def event_stream(self) -> Iterable[tuple[str, dict[str, Any]]]:
        self._emit("snapshot", asdict(self.snapshot()))
        self._emit(
            "gate_state",
            {
                "session_id": self.session_id,
                "gate_state": self.gate_state,
                "enabled": self.config.wake_gate.enabled,
            },
        )
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
                self._check_wake_idle_timeout()
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
                        "gate_state": self.gate_state,
                    }
                    if vad_event.event == "speech_start":
                        self._touch_awake_activity()
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
                "gate_state": self.gate_state,
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
        if self.config.wake_gate.enabled:
            self._process_gate_controlled_session(live_session_id)
        else:
            self._process_live_session(live_session_id)

    def _process_gate_controlled_session(self, live_session_id: str) -> None:
        self._set_state("transcribing")

        try:
            data = self._finish_transcript_only(live_session_id)
            transcript = _reply_text_from_keys(
                data,
                ("transcript", "text", "final_transcript"),
            ).strip()
            self._emit(
                "transcript",
                {
                    **data,
                    "session_id": self.session_id,
                    "live_session_id": live_session_id,
                    "utterance_id": self.active_utterance_id,
                    "transcript": transcript,
                    "is_final": True,
                    "gate_state": self.gate_state,
                },
            )

            if self.gate_state == "waiting_wake":
                if self._matches_wake_phrase(transcript):
                    self._touch_awake_activity()
                    self._set_gate_state(
                        "awake",
                        reason="wake_phrase",
                        transcript=transcript,
                    )
                    self._emit(
                        "wake_detected",
                        {
                            "session_id": self.session_id,
                            "transcript": transcript,
                            "reply": self.config.wake_gate.wake_reply,
                        },
                    )
                else:
                    self._emit(
                        "wake_ignored",
                        {
                            "session_id": self.session_id,
                            "transcript": transcript,
                        },
                    )
                self._cooldown_then_listen()
                return

            if self._matches_exit_phrase(transcript):
                self._set_gate_state(
                    "waiting_wake",
                    reason="exit_phrase",
                    transcript=transcript,
                )
                self._emit(
                    "sleep_detected",
                    {
                        "session_id": self.session_id,
                        "transcript": transcript,
                        "reply": self.config.wake_gate.sleep_reply,
                    },
                )
                self._cooldown_then_listen()
                return

            if not transcript:
                self._emit(
                    "warning",
                    {"message": "没有识别到可发送给对话服务的文本。"},
                )
                self._cooldown_then_listen()
                return

            self._touch_awake_activity()
            self._process_text_chat_session(transcript)
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc) or exc.__class__.__name__
            self._emit("error", {"message": self.last_error})
            self._set_state("listening")
        finally:
            self.active_live_session_id = None
            self.active_utterance_id = None
            self.last_transcript = ""

    def _process_text_chat_session(self, transcript: str) -> None:
        output_audio_seconds = 0.0
        reply_parts: list[str] = []
        try:
            self._set_state("assistant_streaming")
            for item in self.interaction_client.text_stream(
                interaction_session_id=self.interaction_session_id,
                workflow=self.workflow,  # type: ignore[arg-type]
                message=transcript,
                tts_enabled=self.tts_enabled,
            ):
                event = item.event
                data = item.data
                if event == "delta":
                    delta = str(data.get("delta") or "")
                    if delta:
                        reply_parts.append(delta)
                if event == "audio":
                    output_audio_seconds += audio_duration_from_payload(data)
                if event == "done":
                    data = dict(data)
                    data.setdefault("transcript", transcript)
                    if not _reply_text_from_keys(data, ("reply", "response", "answer", "text")):
                        data["reply"] = "".join(reply_parts)
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
                    self._touch_awake_activity()
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

    def _process_live_session(self, live_session_id: str) -> None:
        self._set_state("transcribing")

        output_audio_seconds = 0.0
        try:
            self._set_state("assistant_streaming")
            for item in self.interaction_client.live_finish_stream(
                interaction_session_id=self.interaction_session_id,
                workflow=self.workflow,  # type: ignore[arg-type]
                live_session_id=live_session_id,
                tts_enabled=self.tts_enabled,
            ):
                event = item.event
                data = item.data
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

    def _start_live_session(self) -> str:
        data = self.interaction_client.live_start(
            interaction_session_id=self.interaction_session_id,
            workflow=self.workflow,  # type: ignore[arg-type]
            sample_rate=self.config.vad.sample_rate,
            channels=1,
            audio_format="pcm",
        )
        session_id = data.get("live_session_id") or data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("interaction/live/start did not return a live_session_id")
        return session_id

    def _finish_transcript_only(self, live_session_id: str) -> dict[str, Any]:
        return self.interaction_client.live_finish_transcript(
            interaction_session_id=self.interaction_session_id,
            workflow=self.workflow,  # type: ignore[arg-type]
            live_session_id=live_session_id,
        )

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
            self.interaction_client.live_chunk(
                interaction_session_id=self.interaction_session_id,
                workflow=self.workflow,  # type: ignore[arg-type]
                live_session_id=live_session_id,
                audio_base64=base64.b64encode(chunk).decode("ascii"),
                is_final=chunk_is_final,
            )

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
            data = self.interaction_client.live_transcript(
                interaction_session_id=self.interaction_session_id,
                workflow=self.workflow,  # type: ignore[arg-type]
                live_session_id=live_session_id,
            )
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
                "gate_state": self.gate_state,
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
            self.interaction_client.live_abort(
                interaction_session_id=self.interaction_session_id,
                workflow=self.workflow,  # type: ignore[arg-type]
                live_session_id=live_session_id,
            )
        except Exception:
            pass

    def _cooldown_then_listen(self) -> None:
        self._set_state("cooldown")
        time.sleep(max(0.0, self.config.vad.cooldown_ms / 1000.0))
        self._drain_local_input_queue()
        self.segmenter.reset()
        self._set_state("listening")

    def _check_wake_idle_timeout(self) -> None:
        if not self.config.wake_gate.enabled:
            return
        if self.gate_state != "awake":
            return
        if self.state != "listening" or self.active_live_session_id is not None:
            return
        timeout_seconds = float(self.config.wake_gate.idle_timeout_seconds)
        if timeout_seconds <= 0:
            return
        if time.monotonic() - self.last_awake_activity < timeout_seconds:
            return
        self._set_gate_state("waiting_wake", reason="idle_timeout")
        self.segmenter.reset()
        self._emit(
            "wake_timeout",
            {
                "session_id": self.session_id,
                "timeout_seconds": timeout_seconds,
                "reply": self.config.wake_gate.sleep_reply,
            },
        )

    def _touch_awake_activity(self) -> None:
        self.last_awake_activity = time.monotonic()

    def _set_gate_state(
        self,
        gate_state: AutoVoiceGateState,
        *,
        reason: str = "",
        transcript: str = "",
    ) -> None:
        with self.lock:
            self.gate_state = gate_state
        self._emit(
            "gate_state",
            {
                "session_id": self.session_id,
                "gate_state": gate_state,
                "enabled": self.config.wake_gate.enabled,
                "reason": reason,
                "transcript": transcript,
            },
        )

    def _matches_wake_phrase(self, transcript: str) -> bool:
        return _contains_configured_phrase(
            transcript,
            self.config.wake_gate.wake_phrases,
        )

    def _matches_exit_phrase(self, transcript: str) -> bool:
        return _contains_configured_phrase(
            transcript,
            self.config.wake_gate.exit_phrases,
        )

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


def _reply_text_from_keys(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _contains_configured_phrase(transcript: str, phrases: tuple[str, ...]) -> bool:
    normalized_transcript = _normalize_phrase(transcript)
    if not normalized_transcript:
        return False
    return any(
        normalized_phrase in normalized_transcript
        for phrase in phrases
        if (normalized_phrase := _normalize_phrase(phrase))
    )


def _normalize_phrase(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        char
        for char in folded
        if unicodedata.category(char)[0] not in {"P", "Z"}
    )
