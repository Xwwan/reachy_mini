from __future__ import annotations

import base64
import queue
import threading

import numpy as np

from reachy_dialogue_app.reachy_dialogue_app.audio import robot_output
from reachy_dialogue_app.reachy_dialogue_app.audio.playback import (
    PlaybackMetadata,
    RobotAudioPlaybackScheduler,
    RobotJob,
    _playback_key_from_payload,
    _playback_metadata_from_payload,
)
from reachy_dialogue_app.reachy_dialogue_app.audio.robot_output import (
    RobotJobResult,
    _report_robot_job_playback_result,
)
from reachy_dialogue_app.reachy_dialogue_app import main as dialogue_main


class FakeInteractionClient:
    def __init__(self) -> None:
        self.done_calls: list[dict[str, str]] = []
        self.error_calls: list[dict[str, str]] = []

    def playback_done(self, *, run_id: str, playback_key: str) -> dict[str, str]:
        payload = {"run_id": run_id, "playback_key": playback_key}
        self.done_calls.append(payload)
        return {"ok": "true", **payload}

    def playback_error(
        self,
        *,
        run_id: str,
        playback_key: str,
        error: str,
    ) -> dict[str, str]:
        payload = {
            "run_id": run_id,
            "playback_key": playback_key,
            "error": error,
        }
        self.error_calls.append(payload)
        return {"ok": "true", **payload}


class FakeRobotMedia:
    def __init__(self, output_sample_rate: int = 24000) -> None:
        self.output_sample_rate = output_sample_rate
        self.start_playing_calls = 0
        self.pushed_samples: list[np.ndarray] = []

    def start_playing(self) -> None:
        self.start_playing_calls += 1

    def get_output_audio_samplerate(self) -> int:
        return self.output_sample_rate

    def push_audio_sample(self, samples: np.ndarray) -> None:
        self.pushed_samples.append(samples.copy())


class FakeReachyMini:
    def __init__(self, media: FakeRobotMedia | None = None) -> None:
        self.media = media or FakeRobotMedia()


def test_payload_playback_key_prefers_backend_playback_key() -> None:
    payload = {
        "playback_key": "chat-tts-irun_1",
        "run_id": "irun_1",
        "request_id": "req_1",
        "turn_id": "turn_1",
    }

    assert _playback_key_from_payload(payload) == "chat-tts-irun_1"


def test_playback_metadata_from_payload_captures_interaction_fields() -> None:
    metadata = _playback_metadata_from_payload(
        {
            "playback_key": "chat-tts-irun_1",
            "run_id": "irun_1",
            "interaction_session_id": "isess_1",
            "workflow": "chat",
        }
    )

    assert metadata == PlaybackMetadata(
        playback_key="chat-tts-irun_1",
        run_id="irun_1",
        interaction_session_id="isess_1",
        workflow="chat",
    )


def test_scheduler_emits_final_playback_done_job_when_metadata_is_reportable() -> None:
    jobs: queue.Queue[RobotJob] = queue.Queue()
    scheduler = RobotAudioPlaybackScheduler(jobs)
    metadata = PlaybackMetadata(
        playback_key="chat-tts-irun_1",
        run_id="irun_1",
        interaction_session_id="isess_1",
        workflow="chat",
    )
    audio_base64 = base64.b64encode(b"\x00\x00").decode("ascii")

    scheduler.enqueue_audio(
        "chat-tts-irun_1",
        audio_base64=audio_base64,
        sample_rate=24000,
        playback_metadata=metadata,
    )
    scheduler.complete(
        "chat-tts-irun_1",
        playback_metadata=metadata,
    )

    audio_job = jobs.get_nowait()
    final_job = jobs.get_nowait()

    assert audio_job.audio_bytes == b"\x00\x00"
    assert audio_job.playback_metadata == metadata
    assert audio_job.report_playback_done is False
    assert final_job.audio_bytes is None
    assert final_job.playback_metadata == metadata
    assert final_job.report_playback_done is True


def test_report_robot_job_playback_result_calls_done_only_for_final_job() -> None:
    client = FakeInteractionClient()
    metadata = PlaybackMetadata(playback_key="pb_1", run_id="irun_1")

    audio_job = RobotJob(playback_metadata=metadata)
    final_job = RobotJob(
        playback_metadata=metadata,
        report_playback_done=True,
    )

    assert _report_robot_job_playback_result(
        client,  # type: ignore[arg-type]
        audio_job,
        RobotJobResult(ok=True),
    ) is None
    assert _report_robot_job_playback_result(
        client,  # type: ignore[arg-type]
        final_job,
        RobotJobResult(ok=True),
    ) == {"ok": "true", "run_id": "irun_1", "playback_key": "pb_1"}
    assert client.done_calls == [{"run_id": "irun_1", "playback_key": "pb_1"}]


def test_report_robot_job_playback_result_calls_error_for_failed_chunk() -> None:
    client = FakeInteractionClient()
    job = RobotJob(
        playback_metadata=PlaybackMetadata(playback_key="pb_1", run_id="irun_1")
    )

    result = _report_robot_job_playback_result(
        client,  # type: ignore[arg-type]
        job,
        RobotJobResult(ok=False, error="speaker unavailable"),
    )

    assert result == {
        "ok": "true",
        "run_id": "irun_1",
        "playback_key": "pb_1",
        "error": "speaker unavailable",
    }
    assert client.error_calls == [
        {
            "run_id": "irun_1",
            "playback_key": "pb_1",
            "error": "speaker unavailable",
        }
    ]


def test_handle_robot_job_pushes_pcm_chunk_without_waiting(monkeypatch) -> None:
    media = FakeRobotMedia(output_sample_rate=24000)
    reachy_mini = FakeReachyMini(media)
    metadata = PlaybackMetadata(playback_key="pb_1", run_id="irun_1")
    sleeps: list[float] = []
    audio_bytes = np.array([0, 32767, -32768], dtype="<i2").tobytes()

    monkeypatch.setattr(
        robot_output.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )

    result = robot_output._handle_robot_job(
        reachy_mini,  # type: ignore[arg-type]
        RobotJob(
            audio_bytes=audio_bytes,
            audio_sample_rate=24000,
            playback_metadata=metadata,
        ),
    )

    assert result.ok
    assert sleeps == []
    assert media.start_playing_calls == 1
    assert len(media.pushed_samples) == 1
    np.testing.assert_allclose(
        media.pushed_samples[0],
        np.array([0.0, 32767 / 32768, -1.0], dtype=np.float32),
    )


def test_handle_robot_job_resamples_to_robot_output_rate() -> None:
    media = FakeRobotMedia(output_sample_rate=16000)
    reachy_mini = FakeReachyMini(media)
    audio_bytes = np.zeros(240, dtype="<i2").tobytes()

    result = robot_output._handle_robot_job(
        reachy_mini,  # type: ignore[arg-type]
        RobotJob(audio_bytes=audio_bytes, audio_sample_rate=24000),
    )

    assert result.ok
    assert len(media.pushed_samples) == 1
    assert media.pushed_samples[0].shape == (160,)


def test_final_robot_job_waits_for_streamed_audio_before_done(monkeypatch) -> None:
    media = FakeRobotMedia(output_sample_rate=24000)
    reachy_mini = FakeReachyMini(media)
    metadata = PlaybackMetadata(playback_key="pb_1", run_id="irun_1")
    sleeps: list[float] = []
    monotonic_values = iter([10.0, 10.25])

    monkeypatch.setattr(
        robot_output.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        robot_output.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )

    audio_result = robot_output._handle_robot_job(
        reachy_mini,  # type: ignore[arg-type]
        RobotJob(
            audio_bytes=np.zeros(24000, dtype="<i2").tobytes(),
            audio_sample_rate=24000,
            playback_metadata=metadata,
        ),
    )
    final_result = robot_output._handle_robot_job(
        reachy_mini,  # type: ignore[arg-type]
        RobotJob(playback_metadata=metadata, report_playback_done=True),
    )

    assert audio_result.ok
    assert final_result.ok
    assert sleeps == [0.75]


def test_process_robot_job_reports_done_and_sets_event(monkeypatch) -> None:
    done_event = threading.Event()
    client = FakeInteractionClient()
    metadata = PlaybackMetadata(playback_key="pb_1", run_id="irun_1")
    job = RobotJob(
        done_event=done_event,
        playback_metadata=metadata,
        report_playback_done=True,
    )

    monkeypatch.setattr(
        dialogue_main,
        "_handle_robot_job",
        lambda reachy_mini, job: RobotJobResult(ok=True),
    )

    dialogue_main._process_robot_job(
        object(),  # type: ignore[arg-type]
        job,
        service_url="http://backend.test",
        failed_playback_keys=set(),
        client_factory=lambda service_url: client,  # type: ignore[arg-type]
    )

    assert client.done_calls == [{"run_id": "irun_1", "playback_key": "pb_1"}]
    assert client.error_calls == []
    assert done_event.is_set()


def test_process_robot_job_reports_error_and_remembers_failed_group(monkeypatch) -> None:
    client = FakeInteractionClient()
    metadata = PlaybackMetadata(playback_key="pb_1", run_id="irun_1")
    failed_playback_keys: set[str] = set()
    job = RobotJob(playback_metadata=metadata)

    monkeypatch.setattr(
        dialogue_main,
        "_handle_robot_job",
        lambda reachy_mini, job: RobotJobResult(
            ok=False,
            error="speaker unavailable",
        ),
    )

    dialogue_main._process_robot_job(
        object(),  # type: ignore[arg-type]
        job,
        service_url="http://backend.test",
        failed_playback_keys=failed_playback_keys,
        client_factory=lambda service_url: client,  # type: ignore[arg-type]
    )

    assert client.done_calls == []
    assert client.error_calls == [
        {
            "run_id": "irun_1",
            "playback_key": "pb_1",
            "error": "speaker unavailable",
        }
    ]
    assert failed_playback_keys == {"pb_1"}


def test_process_robot_job_skips_done_after_group_failure(monkeypatch) -> None:
    done_event = threading.Event()
    client = FakeInteractionClient()
    metadata = PlaybackMetadata(playback_key="pb_1", run_id="irun_1")
    failed_playback_keys = {"pb_1"}
    job = RobotJob(
        done_event=done_event,
        playback_metadata=metadata,
        report_playback_done=True,
    )

    monkeypatch.setattr(
        dialogue_main,
        "_handle_robot_job",
        lambda reachy_mini, job: RobotJobResult(ok=True),
    )

    dialogue_main._process_robot_job(
        object(),  # type: ignore[arg-type]
        job,
        service_url="http://backend.test",
        failed_playback_keys=failed_playback_keys,
        client_factory=lambda service_url: client,  # type: ignore[arg-type]
    )

    assert client.done_calls == []
    assert client.error_calls == []
    assert failed_playback_keys == set()
    assert done_event.is_set()
