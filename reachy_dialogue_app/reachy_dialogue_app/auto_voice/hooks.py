from __future__ import annotations

import threading
from typing import Any, Callable

from ..audio.playback import (
    RobotAudioPlaybackScheduler,
    _new_playback_key,
    _optional_int,
)
from ..behavior import (
    _behavior_result_payload,
    _first_ok_module_key,
    _module_config,
    _trigger_behaviors_from_text,
)
from ..core.constants import OUTPUT_SAMPLE_RATE


def _auto_voice_stream_hook_factory(
    playback_scheduler: RobotAudioPlaybackScheduler | None,
    behavior_config: dict[str, Any],
) -> Callable[
    [str],
    Callable[
        [str, dict[str, Any]],
        tuple[list[tuple[str, dict[str, Any]]], threading.Event | None],
    ],
]:
    def factory(session_id: str):
        playback_key = _new_playback_key(f"auto-voice-{session_id}")

        def hook(
            event: str,
            data: dict[str, Any],
        ) -> tuple[list[tuple[str, dict[str, Any]]], threading.Event | None]:
            extras: list[tuple[str, dict[str, Any]]] = []
            if event == "audio":
                audio_base64 = data.get("audio_base64")
                if (
                    playback_scheduler is not None
                    and isinstance(audio_base64, str)
                    and audio_base64
                ):
                    playback_scheduler.enqueue_audio(
                        playback_key,
                        audio_base64=audio_base64,
                        sample_rate=int(
                            data.get("sample_rate")
                            or data.get("audio_sample_rate")
                            or OUTPUT_SAMPLE_RATE
                        ),
                        chunk_index=_optional_int(data.get("chunk_index")),
                        segment_index=_optional_int(data.get("segment_index")),
                    )
                return extras, None

            if event != "done":
                return extras, None

            behavior_results = _trigger_behaviors_from_text(
                str(data.get("reply") or ""),
                behavior_config,
            )
            for result in behavior_results:
                extras.append(("behavior", _behavior_result_payload(result)))

            audio_base64 = data.get("audio_base64")
            if (
                playback_scheduler is not None
                and isinstance(audio_base64, str)
                and audio_base64
            ):
                playback_scheduler.enqueue_audio(
                    playback_key,
                    audio_base64=audio_base64,
                    sample_rate=int(
                        data.get("sample_rate")
                        or data.get("audio_sample_rate")
                        or OUTPUT_SAMPLE_RATE
                    ),
                    chunk_index=_optional_int(data.get("chunk_index")),
                    segment_index=_optional_int(data.get("segment_index")),
                )

            if playback_scheduler is None:
                return extras, None

            done_event = threading.Event()
            playback_scheduler.complete(
                playback_key,
                action_signal=_first_ok_module_key(behavior_results, "action"),
                action_config=_module_config(behavior_config, "action"),
                done_event=done_event,
            )
            return extras, done_event

        return hook

    return factory


