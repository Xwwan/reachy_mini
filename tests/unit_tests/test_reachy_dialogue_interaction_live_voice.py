from __future__ import annotations

import base64

import numpy as np

from reachy_dialogue_app.reachy_dialogue_app.audio.robot_mic import (
    InteractionLiveVoiceSession,
    RobotMicRecorder,
)
from reachy_dialogue_app.reachy_dialogue_app.interaction import SseEvent


class FakeInteractionLiveClient:
    def __init__(self) -> None:
        self.live_start_calls: list[dict] = []
        self.live_chunk_calls: list[dict] = []
        self.live_transcript_calls: list[dict] = []
        self.live_finish_stream_calls: list[dict] = []
        self.live_abort_calls: list[dict] = []
        self.transcript_payload = {
            "transcript": "hello",
            "is_final": False,
            "error": None,
        }
        self.finish_events = [
            SseEvent(
                "transcript",
                {
                    "interaction_session_id": "isess_1",
                    "workflow": "chat",
                    "live_session_id": "live_1",
                    "transcript": "hello",
                    "is_final": True,
                },
            ),
            SseEvent(
                "done",
                {
                    "interaction_session_id": "isess_1",
                    "workflow": "chat",
                    "run_id": "irun_1",
                    "transcript": "hello",
                    "reply": "hi",
                },
            ),
        ]

    def live_start(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        sample_rate: int,
        channels: int,
        audio_format: str,
    ) -> dict:
        self.live_start_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "sample_rate": sample_rate,
                "channels": channels,
                "audio_format": audio_format,
            }
        )
        return {
            "interaction_session_id": interaction_session_id,
            "workflow": workflow,
            "live_session_id": "live_1",
            "session_id": "live_1",
            "sample_rate": sample_rate,
        }

    def live_chunk(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        live_session_id: str,
        audio_base64: str,
        is_final: bool,
    ) -> dict:
        self.live_chunk_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
                "audio_base64": audio_base64,
                "is_final": is_final,
            }
        )
        return {"ok": True, "accepted_bytes": len(base64.b64decode(audio_base64))}

    def live_transcript(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        live_session_id: str,
    ) -> dict:
        self.live_transcript_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
            }
        )
        return self.transcript_payload

    def live_finish_stream(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        live_session_id: str,
        tts_enabled: bool,
    ):
        self.live_finish_stream_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
                "tts_enabled": tts_enabled,
            }
        )
        yield from self.finish_events

    def live_abort(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        live_session_id: str,
    ) -> dict:
        self.live_abort_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
            }
        )
        return {"ok": True}


class FakeMedia:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def get_input_audio_samplerate(self) -> int:
        return 16000

    def get_input_channels(self) -> int:
        return 1

    def start_recording(self) -> None:
        self.started = True

    def stop_recording(self) -> None:
        self.stopped = True

    def get_audio_sample(self):
        return None


class FakeReachy:
    def __init__(self) -> None:
        self.media = FakeMedia()


def test_interaction_live_voice_start_uses_interaction_live_start() -> None:
    fake_client = FakeInteractionLiveClient()

    session = InteractionLiveVoiceSession.start(
        "http://backend.test",
        interaction_session_id="isess_1",
        workflow="chat",
        sample_rate=16000,
        client_factory=lambda service_url: fake_client,  # type: ignore[arg-type]
    )
    session.abort()

    assert session.live_session_id == "live_1"
    assert fake_client.live_start_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "sample_rate": 16000,
            "channels": 1,
            "audio_format": "pcm",
        }
    ]


def test_interaction_live_voice_posts_buffered_pcm_on_finish_stream() -> None:
    fake_client = FakeInteractionLiveClient()
    session = InteractionLiveVoiceSession.start(
        "http://backend.test",
        interaction_session_id="isess_1",
        workflow="chat",
        sample_rate=16000,
        client_factory=lambda service_url: fake_client,  # type: ignore[arg-type]
    )

    session.submit_pcm(b"\x01\x02\x03\x04")
    events = list(session.finish_stream(tts_enabled=True))

    assert fake_client.live_chunk_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
            "audio_base64": "AQIDBA==",
            "is_final": False,
        }
    ]
    assert fake_client.live_finish_stream_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
            "tts_enabled": True,
        }
    ]
    assert events[-1]["event"] == "done"
    snapshot = session.debug_snapshot()
    assert snapshot["transcript"] == "hello"
    assert snapshot["is_final"] is True


def test_interaction_live_voice_get_transcript_uses_interaction_params() -> None:
    fake_client = FakeInteractionLiveClient()
    session = InteractionLiveVoiceSession.start(
        "http://backend.test",
        interaction_session_id="isess_1",
        workflow="onboarding",
        sample_rate=16000,
        client_factory=lambda service_url: fake_client,  # type: ignore[arg-type]
    )

    transcript = session.get_transcript()
    session.abort()

    assert transcript.text == "hello"
    assert transcript.is_final is False
    assert fake_client.live_transcript_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "onboarding",
            "live_session_id": "live_1",
        }
    ]


def test_interaction_live_voice_abort_uses_interaction_abort() -> None:
    fake_client = FakeInteractionLiveClient()
    session = InteractionLiveVoiceSession.start(
        "http://backend.test",
        interaction_session_id="isess_1",
        workflow="chat",
        sample_rate=16000,
        client_factory=lambda service_url: fake_client,  # type: ignore[arg-type]
    )

    session.abort()

    assert fake_client.live_abort_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
        }
    ]


def test_robot_mic_recorder_can_start_and_stop_interaction_session(
    monkeypatch,
) -> None:
    fake_client = FakeInteractionLiveClient()
    reachy = FakeReachy()
    recorder = RobotMicRecorder(reachy)  # type: ignore[arg-type]
    first_sample = np.full(6000, 0.01, dtype=np.float32)
    monkeypatch.setattr(
        "reachy_dialogue_app.reachy_dialogue_app.audio.robot_mic._wait_for_robot_audio_sample",
        lambda reachy_mini: first_sample,
    )
    monkeypatch.setattr(
        InteractionLiveVoiceSession,
        "start",
        classmethod(
            lambda cls, service_url, *, interaction_session_id, workflow, sample_rate, client_factory=None: cls(
                client=fake_client,  # type: ignore[arg-type]
                interaction_session_id=interaction_session_id,
                workflow=workflow,
                live_session_id="live_1",
                sample_rate=sample_rate,
            )
        ),
    )

    recorder.start_interaction(
        service_url="http://backend.test",
        interaction_session_id="isess_1",
        workflow="chat",
    )
    recording, session = recorder.stop_interaction_for_stream()
    try:
        events = list(session.finish_stream(tts_enabled=True))
    finally:
        recorder.finish_reply_processing(session)

    assert reachy.media.started is True
    assert reachy.media.stopped is True
    assert recording.sample_rate == 16000
    assert recording.byte_count == 12000
    assert session.interaction_session_id == "isess_1"
    assert session.workflow == "chat"
    assert fake_client.live_chunk_calls[0]["interaction_session_id"] == "isess_1"
    assert fake_client.live_finish_stream_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
            "tts_enabled": True,
        }
    ]
    assert events[-1]["event"] == "done"
