from __future__ import annotations

import threading
from typing import Any, Callable

from ..audio.playback import (
    NullPlaybackSink,
    RobotAudioPlaybackScheduler,
    RobotPlaybackSink,
    _new_playback_key,
    _optional_int,
    _playback_metadata_from_payload,
)
from ..behavior import (
    BehaviorTriggerTracker,
    _behavior_result_payload,
    _first_ok_module_key,
    _module_config,
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
    playback_sink = (
        RobotPlaybackSink(playback_scheduler)
        if playback_scheduler is not None
        else NullPlaybackSink()
    )

    def factory(session_id: str):
        playback_key = _new_playback_key(f"auto-voice-{session_id}")
        behavior_tracker = BehaviorTriggerTracker(behavior_config)

        def submit_behavior_actions(behavior_results: list[Any]) -> None:
            if not playback_sink.active:
                return
            playback_sink.submit_action(
                action_signal=_first_ok_module_key(behavior_results, "action"),
                action_config=_module_config(behavior_config, "action"),
            )

        def hook(
            event: str,
            data: dict[str, Any],
        ) -> tuple[list[tuple[str, dict[str, Any]]], threading.Event | None]:
            extras: list[tuple[str, dict[str, Any]]] = []
            playback_metadata = _playback_metadata_from_payload(data, playback_key)
            key = playback_metadata.playback_key
            if event == "audio":
                audio_base64 = data.get("audio_base64")
                if (
                    playback_sink.active
                    and isinstance(audio_base64, str)
                    and audio_base64
                ):
                    playback_sink.enqueue_audio(
                        key,
                        audio_base64=audio_base64,
                        sample_rate=int(
                            data.get("sample_rate")
                            or data.get("audio_sample_rate")
                            or OUTPUT_SAMPLE_RATE
                        ),
                        chunk_index=_optional_int(data.get("chunk_index")),
                        segment_index=_optional_int(data.get("segment_index")),
                        playback_metadata=playback_metadata,
                    )
                return extras, None

            if event == "delta":
                behavior_results = behavior_tracker.trigger_from_fragment(
                    str(data.get("delta") or "")
                )
                submit_behavior_actions(behavior_results)
                for result in behavior_results:
                    extras.append(("behavior", _behavior_result_payload(result)))
                return extras, None

            if event != "done":
                return extras, None

            behavior_results = behavior_tracker.trigger_from_text(
                str(data.get("reply") or "")
            )
            submit_behavior_actions(behavior_results)
            for result in behavior_results:
                extras.append(("behavior", _behavior_result_payload(result)))

            audio_base64 = data.get("audio_base64")
            if (
                playback_sink.active
                and isinstance(audio_base64, str)
                and audio_base64
            ):
                playback_sink.enqueue_audio(
                    key,
                    audio_base64=audio_base64,
                    sample_rate=int(
                        data.get("sample_rate")
                        or data.get("audio_sample_rate")
                        or OUTPUT_SAMPLE_RATE
                    ),
                    chunk_index=_optional_int(data.get("chunk_index")),
                    segment_index=_optional_int(data.get("segment_index")),
                    playback_metadata=playback_metadata,
                )

            if not playback_sink.active:
                return extras, None

            done_event = threading.Event()
            playback_sink.complete(
                key,
                done_event=done_event,
                playback_metadata=playback_metadata,
            )
            return extras, done_event

        return hook

    return factory
