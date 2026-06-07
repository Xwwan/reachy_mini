import queue
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from reachy_dialogue_app.reachy_dialogue_app.auto_voice import manager as manager_module
from reachy_dialogue_app.reachy_dialogue_app.auto_voice.hooks import (
    _auto_voice_stream_hook_factory,
)
from reachy_dialogue_app.reachy_dialogue_app.auto_voice import session as session_module
from reachy_dialogue_app.reachy_dialogue_app.auto_voice.config import AutoVoiceConfig
from reachy_dialogue_app.reachy_dialogue_app.auto_voice.session import AutoVoiceSession
from reachy_dialogue_app.reachy_dialogue_app.auto_voice.types import WakeGateConfig
from reachy_dialogue_app.reachy_dialogue_app.audio.playback import (
    RobotAudioPlaybackScheduler,
    RobotJob,
)
from reachy_dialogue_app.reachy_dialogue_app.vad import VadConfig
from reachy_dialogue_app.reachy_dialogue_app.interaction.sse import SseEvent


class FakeInteractionClient:
    create_session_calls = []

    def __init__(self, service_url: str) -> None:
        self.service_url = service_url

    def create_session(self, **kwargs):
        self.create_session_calls.append(kwargs)
        return {
            "interaction_session_id": "isess_auto",
            "workflow": kwargs["workflow"],
        }


class FakeAutoVoiceSession:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.workflow = kwargs["workflow"]
        self.interaction_session_id = kwargs["interaction_session_id"]

    def snapshot(self):
        return SimpleNamespace(
            session_id=self.kwargs["session_id"],
            mode=self.kwargs["mode"],
            state="listening",
            conversation_id=self.kwargs["conversation_id"],
            tts_enabled=self.kwargs["tts_enabled"],
            gate_state="awake",
            wake_gate_enabled=False,
        )


def _auto_voice_config() -> AutoVoiceConfig:
    return AutoVoiceConfig(
        vad=VadConfig(sample_rate=16000, cooldown_ms=1),
        wake_gate=WakeGateConfig(enabled=False),
    )


def test_auto_voice_manager_creates_interaction_session(monkeypatch) -> None:
    FakeInteractionClient.create_session_calls = []
    monkeypatch.setattr(manager_module, "InteractionApiClient", FakeInteractionClient)
    monkeypatch.setattr(manager_module, "AutoVoiceSession", FakeAutoVoiceSession)

    manager = manager_module.AutoVoiceManager(
        model_path=manager_module.Path("/tmp/fake-vad.onnx"),
        config=_auto_voice_config(),
        service_url_getter=lambda: "http://backend.test",
    )

    session = manager.start(
        mode="local",
        conversation_id="conversation-auto",
        tts_enabled=True,
        workflow="onboarding",
    )

    assert session.interaction_session_id == "isess_auto"
    assert session.workflow == "onboarding"
    assert FakeInteractionClient.create_session_calls == [
        {
            "workflow": "onboarding",
            "conversation_id": "conversation-auto",
            "input_mode": "auto",
            "tts_enabled": True,
        }
    ]


class FakeLiveClient:
    def __init__(self) -> None:
        self.calls = []

    def live_start(self, **kwargs):
        self.calls.append(("live_start", kwargs))
        return {"live_session_id": "live_auto"}

    def live_chunk(self, **kwargs):
        self.calls.append(("live_chunk", kwargs))
        return {"ok": True, "accepted_bytes": 1}

    def live_transcript(self, **kwargs):
        self.calls.append(("live_transcript", kwargs))
        return {
            "interaction_session_id": kwargs["interaction_session_id"],
            "workflow": kwargs["workflow"],
            "live_session_id": kwargs["live_session_id"],
            "transcript": "你好",
            "is_final": False,
        }

    def live_abort(self, **kwargs):
        self.calls.append(("live_abort", kwargs))
        return {"ok": True}

    def live_finish_transcript(self, **kwargs):
        self.calls.append(("live_finish_transcript", kwargs))
        return {
            "interaction_session_id": kwargs["interaction_session_id"],
            "workflow": kwargs["workflow"],
            "live_session_id": kwargs["live_session_id"],
            "transcript": "你好",
            "is_final": True,
        }

    def live_finish_stream(self, **kwargs):
        self.calls.append(("live_finish_stream", kwargs))
        yield SseEvent(
            "audio",
            {
                "interaction_session_id": kwargs["interaction_session_id"],
                "workflow": kwargs["workflow"],
                "run_id": "irun_auto",
                "playback_key": "auto-tts-irun_auto",
                "audio_base64": "AAAA",
                "sample_rate": 24000,
            },
        )
        yield SseEvent(
            "done",
            {
                "interaction_session_id": kwargs["interaction_session_id"],
                "workflow": kwargs["workflow"],
                "run_id": "irun_auto",
                "playback_key": "auto-tts-irun_auto",
                "reply": "你好呀",
            },
        )


def _make_session(client: FakeLiveClient) -> AutoVoiceSession:
    session = AutoVoiceSession.__new__(AutoVoiceSession)
    session.session_id = "auto_1"
    session.mode = "local"
    session.service_url = "http://backend.test/"
    session.conversation_id = "conversation-auto"
    session.interaction_session_id = "isess_auto"
    session.workflow = "chat"
    session.tts_enabled = True
    session.interaction_client = client
    session.config = _auto_voice_config()
    session.events = queue.Queue()
    session.lock = threading.Lock()
    session.state = "listening"
    session.active_live_session_id = "live_auto"
    session.active_utterance_id = "utt_1"
    session.last_transcript = ""
    session.last_transcript_poll = 0.0
    session.last_awake_activity = 0.0
    session.gate_state = "awake"
    return session


def test_auto_voice_session_uses_interaction_live_methods() -> None:
    client = FakeLiveClient()
    session = _make_session(client)

    live_session_id = session._start_live_session()
    session._send_audio_to_live_session(
        live_session_id,
        np.array([0.0, 0.25, -0.25], dtype=np.float32),
        is_final=False,
    )
    session._emit_live_transcript(force=True)
    transcript = session._finish_transcript_only(live_session_id)

    assert live_session_id == "live_auto"
    assert transcript["transcript"] == "你好"
    assert [call[0] for call in client.calls] == [
        "live_start",
        "live_chunk",
        "live_transcript",
        "live_finish_transcript",
    ]
    for _, kwargs in client.calls:
        assert kwargs["interaction_session_id"] == "isess_auto"
        assert kwargs["workflow"] == "chat"


def test_auto_voice_finish_stream_uses_interaction_events() -> None:
    client = FakeLiveClient()
    session = _make_session(client)
    emitted = []
    barriers = []

    def stream_hook(event, data):
        barrier = threading.Event()
        barrier.set()
        barriers.append((event, data, barrier))
        return [("hooked", {"event": event})], barrier

    session.stream_hook = stream_hook
    session._cooldown_then_listen = lambda: session._set_state("listening")

    session._process_live_session("live_auto")

    while not session.events.empty():
        emitted.append(session.events.get_nowait())

    assert client.calls[0] == (
        "live_finish_stream",
        {
            "interaction_session_id": "isess_auto",
            "workflow": "chat",
            "live_session_id": "live_auto",
            "tts_enabled": True,
        },
    )
    assert [item[0] for item in barriers] == ["audio", "done"]
    assert ("audio", barriers[0][1]) in emitted
    assert ("done", barriers[1][1]) in emitted
    assert any(event == "playback_done" for event, _ in emitted)


def test_auto_voice_hook_triggers_split_action_tag_before_done() -> None:
    jobs: queue.Queue[RobotJob] = queue.Queue()
    scheduler = RobotAudioPlaybackScheduler(jobs)
    behavior_config = {
        "enabled": True,
        "modules": {
            "action": {
                "enabled": True,
                "tag_names": ["act"],
                "trigger_mode": "function",
                "triggers": "*",
            }
        },
    }
    hook = _auto_voice_stream_hook_factory(scheduler, behavior_config)("session_1")

    extras, barrier = hook("delta", {"delta": "准备动作 [act:ha"})
    assert extras == []
    assert barrier is None
    assert jobs.empty()

    extras, barrier = hook("delta", {"delta": "ppy]"})
    action_job = jobs.get_nowait()

    assert barrier is None
    assert extras == [
        (
            "behavior",
            {
                "matched": True,
                "module": "action",
                "tag": "act",
                "key": "happy",
                "url": None,
                "triggered": True,
                "ok": True,
                "status_code": None,
                "error": None,
            },
        )
    ]
    assert action_job.action_signal == "happy"

    extras, barrier = hook(
        "done",
        {
            "reply": "准备动作 [act:happy]",
            "run_id": "irun_auto",
            "playback_key": "pb_auto",
        },
    )
    final_job = jobs.get_nowait()

    assert extras == []
    assert barrier is not None
    assert final_job.action_signal is None
    assert final_job.report_playback_done is True


def test_auto_voice_playback_wait_subtracts_streamed_audio_time(monkeypatch) -> None:
    client = FakeLiveClient()
    session = _make_session(client)
    sleeps = []

    class FakeTime:
        @staticmethod
        def monotonic() -> float:
            return 13.0

        @staticmethod
        def sleep(seconds: float) -> None:
            sleeps.append(seconds)

    monkeypatch.setattr(session_module, "time", FakeTime)

    session.config.playback_wait_grace_seconds = 0.1
    session.config.playback_wait_max_seconds = 0.0
    session._wait_for_output_playback(
        barrier=None,
        output_audio_seconds=5.0,
        output_audio_started_at=10.0,
    )

    assert sleeps == [pytest.approx(2.1)]


def test_auto_voice_playback_barrier_timeout_uses_audio_estimate(monkeypatch) -> None:
    client = FakeLiveClient()
    session = _make_session(client)
    waits = []

    class FakeBarrier:
        def wait(self, timeout: float) -> bool:
            waits.append(timeout)
            return False

    class FakeTime:
        @staticmethod
        def monotonic() -> float:
            return 13.0

        @staticmethod
        def sleep(seconds: float) -> None:
            raise AssertionError("barrier path should not call sleep")

    monkeypatch.setattr(session_module, "time", FakeTime)

    session.config.service_timeout_seconds = 120
    session.config.playback_wait_grace_seconds = 0.1
    session.config.playback_wait_max_seconds = 0.0
    session._wait_for_output_playback(
        barrier=FakeBarrier(),
        output_audio_seconds=5.0,
        output_audio_started_at=10.0,
    )

    assert waits == [pytest.approx(2.1)]
    assert session.events.get_nowait() == (
        "warning",
        {
            "message": "robot playback completion timed out; reopening auto voice input",
            "timeout_seconds": pytest.approx(2.1),
        },
    )
