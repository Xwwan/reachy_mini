from __future__ import annotations

import base64
import os
import tempfile
import time
import wave

from dataclasses import dataclass

from ..behavior import _play_action_signal
from ..interaction import InteractionApiClient
from .playback import RobotJob


@dataclass(frozen=True)
class RobotJobResult:
    ok: bool
    error: str | None = None


def _handle_robot_job(reachy_mini: ReachyMini, job: RobotJob) -> RobotJobResult:
    wav_path = None
    playback_seconds = 0.0
    try:
        audio_bytes = job.audio_bytes
        if audio_bytes is None and job.audio_base64:
            audio_bytes = base64.b64decode(job.audio_base64)
        if audio_bytes:
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
        return RobotJobResult(ok=True)
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        print(f"Robot response failed: {message}")
        return RobotJobResult(ok=False, error=message)
    finally:
        if wav_path is not None:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


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
