from __future__ import annotations

import base64
import time

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import signal

from ..behavior import _play_action_signal
from ..interaction import InteractionApiClient
from .playback import RobotJob


@dataclass(frozen=True)
class RobotJobResult:
    ok: bool
    error: str | None = None


def _handle_robot_job(reachy_mini: ReachyMini, job: RobotJob) -> RobotJobResult:
    try:
        audio_bytes = job.audio_bytes
        if audio_bytes is None and job.audio_base64:
            audio_bytes = base64.b64decode(job.audio_base64)
        if audio_bytes:
            _push_pcm16_audio(reachy_mini, job, audio_bytes)
        else:
            _wait_for_streamed_audio(reachy_mini, job)
        if job.action_signal:
            _play_action_signal(reachy_mini, job.action_signal, job.action_config)
        return RobotJobResult(ok=True)
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        print(f"Robot response failed: {message}")
        return RobotJobResult(ok=False, error=message)


def _report_robot_job_playback_result(
    client: InteractionApiClient,
    job: RobotJob,
    result: RobotJobResult,
) -> dict | None:
    metadata = job.playback_metadata
    if metadata is None or not metadata.playback_key or not metadata.run_id:
        return None
    if result.ok:
        if not job.report_playback_done:
            return None
        return client.playback_done(
            run_id=metadata.run_id,
            playback_key=metadata.playback_key,
        )
    return client.playback_error(
        run_id=metadata.run_id,
        playback_key=metadata.playback_key,
        error=result.error or "robot playback failed",
    )


def _push_pcm16_audio(
    reachy_mini: ReachyMini,
    job: RobotJob,
    audio_bytes: bytes,
) -> None:
    media = reachy_mini.media
    _ensure_streaming_playback_started(media)

    source_rate = max(1, int(job.audio_sample_rate))
    output_rate = _output_sample_rate(media, source_rate)
    samples = _pcm16_bytes_to_float32(audio_bytes)
    samples = _resample_if_needed(samples, source_rate, output_rate)
    if samples.size == 0:
        return

    media.push_audio_sample(samples)
    _remember_streamed_audio_deadline(
        reachy_mini,
        _job_playback_key(job),
        samples.shape[0] / float(output_rate),
    )


def _ensure_streaming_playback_started(media: Any) -> None:
    if getattr(media, "_reachy_dialogue_streaming_playback_started", False):
        return
    media.start_playing()
    setattr(media, "_reachy_dialogue_streaming_playback_started", True)


def _output_sample_rate(media: Any, fallback: int) -> int:
    try:
        output_rate = int(media.get_output_audio_samplerate())
    except Exception:
        output_rate = fallback
    return output_rate if output_rate > 0 else fallback


def _pcm16_bytes_to_float32(audio_bytes: bytes) -> np.ndarray:
    pcm = np.frombuffer(audio_bytes, dtype="<i2")
    if pcm.size == 0:
        return np.zeros(0, dtype=np.float32)
    return (pcm.astype(np.float32) / 32768.0).clip(-1.0, 1.0)


def _resample_if_needed(
    samples: np.ndarray,
    source_rate: int,
    output_rate: int,
) -> np.ndarray:
    if source_rate == output_rate or samples.size == 0:
        return samples
    gcd = np.gcd(source_rate, output_rate)
    return signal.resample_poly(
        samples,
        output_rate // gcd,
        source_rate // gcd,
    ).astype(np.float32, copy=False)


def _remember_streamed_audio_deadline(
    reachy_mini: ReachyMini,
    playback_key: str,
    duration_seconds: float,
) -> None:
    deadlines = _stream_deadlines(reachy_mini)
    now = time.monotonic()
    deadlines[playback_key] = max(now, deadlines.get(playback_key, now)) + max(
        0.0,
        duration_seconds,
    )


def _wait_for_streamed_audio(reachy_mini: ReachyMini, job: RobotJob) -> None:
    playback_key = _job_playback_key(job)
    deadlines = _stream_deadlines(reachy_mini)
    deadline = deadlines.pop(playback_key, None)
    if deadline is None:
        return
    time.sleep(max(0.0, deadline - time.monotonic()))


def _stream_deadlines(reachy_mini: ReachyMini) -> dict[str, float]:
    deadlines = getattr(reachy_mini, "_reachy_dialogue_stream_deadlines", None)
    if not isinstance(deadlines, dict):
        deadlines = {}
        setattr(reachy_mini, "_reachy_dialogue_stream_deadlines", deadlines)
    return deadlines


def _job_playback_key(job: RobotJob) -> str:
    metadata = job.playback_metadata
    if metadata is not None and metadata.playback_key:
        return metadata.playback_key
    return "__default__"
