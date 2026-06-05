from __future__ import annotations

import base64
import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from ..core.constants import OUTPUT_SAMPLE_RATE


def _new_playback_key(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


@dataclass(frozen=True)
class PlaybackMetadata:
    playback_key: str | None = None
    run_id: str | None = None
    interaction_session_id: str | None = None
    workflow: str | None = None


@dataclass
class RobotJob:
    audio_base64: str | None = None
    audio_bytes: bytes | None = None
    audio_sample_rate: int = OUTPUT_SAMPLE_RATE
    action_signal: str | None = None
    action_config: dict[str, Any] | None = None
    done_event: threading.Event | None = None
    playback_metadata: PlaybackMetadata | None = None
    report_playback_done: bool = False


@dataclass
class RobotAudioChunk:
    audio_bytes: bytes
    sample_rate: int
    chunk_index: int | None
    segment_index: int | None
    arrival_index: int


@dataclass
class RobotAudioPlaybackGroup:
    key: str
    chunks: list[RobotAudioChunk]
    completed: bool = False
    emitted_count: int = 0
    final_job_queued: bool = False
    action_signal: str | None = None
    action_config: dict[str, Any] | None = None
    done_event: threading.Event | None = None
    playback_metadata: PlaybackMetadata = field(default_factory=PlaybackMetadata)


class RobotAudioPlaybackScheduler:
    """Stream the current reply immediately while buffering later replies."""

    def __init__(
        self,
        jobs: queue.Queue[RobotJob],
        *,
        action_jobs: queue.Queue[RobotJob] | None = None,
    ) -> None:
        self.jobs = jobs
        self.action_jobs = action_jobs or jobs
        self.lock = threading.Lock()
        self.groups: dict[str, RobotAudioPlaybackGroup] = {}
        self.order: list[str] = []
        self.arrival_index = 0

    def enqueue_audio(
        self,
        key: str | None,
        *,
        audio_base64: str,
        sample_rate: int,
        chunk_index: int | None = None,
        segment_index: int | None = None,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> str:
        playback_key = key or _new_playback_key("robot-audio")
        audio_bytes = base64.b64decode(audio_base64)
        with self.lock:
            group = self._group_locked(playback_key, playback_metadata)
            self.arrival_index += 1
            group.chunks.append(
                RobotAudioChunk(
                    audio_bytes=audio_bytes,
                    sample_rate=sample_rate,
                    chunk_index=chunk_index,
                    segment_index=segment_index,
                    arrival_index=self.arrival_index,
                )
            )
            self._drain_locked()
        return playback_key

    def complete(
        self,
        key: str | None,
        *,
        action_signal: str | None = None,
        action_config: dict[str, Any] | None = None,
        done_event: threading.Event | None = None,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> str:
        playback_key = key or _new_playback_key("robot-audio")
        with self.lock:
            group = self._group_locked(playback_key, playback_metadata)
            group.completed = True
            group.action_signal = action_signal
            group.action_config = action_config
            group.done_event = done_event
            self._drain_locked()
        return playback_key

    def submit_complete(
        self,
        *,
        audio_base64: str | None = None,
        audio_sample_rate: int = OUTPUT_SAMPLE_RATE,
        action_signal: str | None = None,
        action_config: dict[str, Any] | None = None,
        done_event: threading.Event | None = None,
        key: str | None = None,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> str:
        playback_key = key or _new_playback_key("robot-audio")
        if audio_base64:
            self.enqueue_audio(
                playback_key,
                audio_base64=audio_base64,
                sample_rate=audio_sample_rate,
                playback_metadata=playback_metadata,
            )
        return self.complete(
            playback_key,
            action_signal=action_signal,
            action_config=action_config,
            done_event=done_event,
            playback_metadata=playback_metadata,
        )

    def submit_action(
        self,
        *,
        action_signal: str | None,
        action_config: dict[str, Any] | None = None,
    ) -> None:
        if not action_signal:
            return
        self.action_jobs.put(
            RobotJob(
                action_signal=action_signal,
                action_config=action_config,
            )
        )

    def abort(self, key: str | None) -> None:
        if not key:
            return
        with self.lock:
            self.groups.pop(key, None)
            self.order = [queued_key for queued_key in self.order if queued_key != key]
            self._drain_locked()

    def _group_locked(
        self,
        key: str,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> RobotAudioPlaybackGroup:
        group = self.groups.get(key)
        normalized_metadata = _normalize_playback_metadata(
            key,
            playback_metadata,
        )
        if group is None:
            group = RobotAudioPlaybackGroup(
                key=key,
                chunks=[],
                playback_metadata=normalized_metadata,
            )
            self.groups[key] = group
            self.order.append(key)
        else:
            group.playback_metadata = _merge_playback_metadata(
                group.playback_metadata,
                normalized_metadata,
            )
        return group

    def _drain_locked(self) -> None:
        while self.order:
            key = self.order[0]
            group = self.groups.get(key)
            if group is None:
                self.order.pop(0)
                continue

            if group.emitted_count == 0 and len(group.chunks) > 1:
                group.chunks.sort(
                    key=lambda chunk: (
                        chunk.segment_index
                        if chunk.segment_index is not None
                        else 0,
                        chunk.chunk_index
                        if chunk.chunk_index is not None
                        else chunk.arrival_index,
                        chunk.arrival_index,
                    )
                )
            while group.emitted_count < len(group.chunks):
                chunk = group.chunks[group.emitted_count]
                group.emitted_count += 1
                self.jobs.put(
                    RobotJob(
                        audio_bytes=chunk.audio_bytes,
                        audio_sample_rate=chunk.sample_rate,
                        playback_metadata=group.playback_metadata,
                    )
                )

            if not group.completed:
                return

            if not group.final_job_queued:
                group.final_job_queued = True
                can_report_done = _can_report_playback_result(
                    group.playback_metadata
                )
                if (
                    group.action_signal
                    or group.done_event is not None
                    or can_report_done
                ):
                    self.jobs.put(
                        RobotJob(
                            action_signal=group.action_signal,
                            action_config=group.action_config,
                            done_event=group.done_event,
                            playback_metadata=group.playback_metadata,
                            report_playback_done=can_report_done,
                        )
                    )

            self.order.pop(0)
            self.groups.pop(key, None)


class PlaybackSink(Protocol):
    name: str
    active: bool

    def enqueue_audio(
        self,
        key: str | None,
        *,
        audio_base64: str,
        sample_rate: int,
        chunk_index: int | None = None,
        segment_index: int | None = None,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> str:
        ...

    def complete(
        self,
        key: str | None,
        *,
        action_signal: str | None = None,
        action_config: dict[str, Any] | None = None,
        done_event: threading.Event | None = None,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> str:
        ...

    def abort(self, key: str | None) -> None:
        ...

    def submit_action(
        self,
        *,
        action_signal: str | None,
        action_config: dict[str, Any] | None = None,
    ) -> None:
        ...


@dataclass(frozen=True)
class RobotPlaybackSink:
    scheduler: RobotAudioPlaybackScheduler
    name: str = "robot"
    active: bool = True

    def enqueue_audio(
        self,
        key: str | None,
        *,
        audio_base64: str,
        sample_rate: int,
        chunk_index: int | None = None,
        segment_index: int | None = None,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> str:
        return self.scheduler.enqueue_audio(
            key,
            audio_base64=audio_base64,
            sample_rate=sample_rate,
            chunk_index=chunk_index,
            segment_index=segment_index,
            playback_metadata=playback_metadata,
        )

    def complete(
        self,
        key: str | None,
        *,
        action_signal: str | None = None,
        action_config: dict[str, Any] | None = None,
        done_event: threading.Event | None = None,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> str:
        return self.scheduler.complete(
            key,
            action_signal=action_signal,
            action_config=action_config,
            done_event=done_event,
            playback_metadata=playback_metadata,
        )

    def abort(self, key: str | None) -> None:
        self.scheduler.abort(key)

    def submit_action(
        self,
        *,
        action_signal: str | None,
        action_config: dict[str, Any] | None = None,
    ) -> None:
        self.scheduler.submit_action(
            action_signal=action_signal,
            action_config=action_config,
        )


@dataclass(frozen=True)
class NullPlaybackSink:
    name: str = "null"
    active: bool = False

    def enqueue_audio(
        self,
        key: str | None,
        *,
        audio_base64: str,
        sample_rate: int,
        chunk_index: int | None = None,
        segment_index: int | None = None,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> str:
        return (
            key
            or (playback_metadata.playback_key if playback_metadata else None)
            or _new_playback_key("null-audio")
        )

    def complete(
        self,
        key: str | None,
        *,
        action_signal: str | None = None,
        action_config: dict[str, Any] | None = None,
        done_event: threading.Event | None = None,
        playback_metadata: PlaybackMetadata | None = None,
    ) -> str:
        if done_event is not None:
            done_event.set()
        return (
            key
            or (playback_metadata.playback_key if playback_metadata else None)
            or _new_playback_key("null-audio")
        )

    def abort(self, key: str | None) -> None:
        return None

    def submit_action(
        self,
        *,
        action_signal: str | None,
        action_config: dict[str, Any] | None = None,
    ) -> None:
        return None


def _payload_playback_key(payload: dict[str, Any]) -> str | None:
    explicit_playback_key = _payload_string(payload, "playback_key")
    if explicit_playback_key:
        return explicit_playback_key

    request_id = _payload_string(payload, "request_id") or _payload_string(
        payload, "parent_request_id"
    )
    turn_id = (
        _payload_string(payload, "followup_turn_id")
        or _payload_string(payload, "assistant_turn_id")
        or _payload_string(payload, "reply_turn_id")
        or _payload_string(payload, "turn_id")
    )
    if request_id and turn_id:
        return f"request:{request_id}:turn:{turn_id}"
    if request_id:
        return f"request:{request_id}"
    run_id = _payload_string(payload, "run_id")
    if run_id:
        return f"run:{run_id}"
    conversation_id = _payload_string(payload, "conversation_id")
    if conversation_id and turn_id:
        return f"conversation:{conversation_id}:turn:{turn_id}"
    return None


def _payload_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _playback_key_from_payload(
    payload: dict[str, Any],
    fallback: str | None = None,
) -> str:
    return (
        _payload_playback_key(payload)
        or fallback
        or _new_playback_key("robot-audio")
    )


def _playback_metadata_from_payload(
    payload: dict[str, Any],
    fallback: str | None = None,
) -> PlaybackMetadata:
    playback_key = _playback_key_from_payload(payload, fallback)
    return PlaybackMetadata(
        playback_key=playback_key,
        run_id=_payload_string(payload, "run_id"),
        interaction_session_id=_payload_string(payload, "interaction_session_id"),
        workflow=_payload_string(payload, "workflow"),
    )


def _followup_playback_group_id(
    payload: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> str:
    candidates = _followup_playback_group_ids(payload)
    if existing:
        for candidate in candidates:
            if candidate in existing:
                return candidate
    return candidates[0]


def _followup_playback_group_ids(payload: dict[str, Any]) -> list[str]:
    return _unique_strings(
        [
            _payload_playback_key(payload),
            _payload_string(payload, "playback_key"),
            _payload_string(payload, "request_id"),
            _payload_string(payload, "parent_request_id"),
            _payload_string(payload, "run_id"),
            _payload_string(payload, "conversation_id"),
            "followup-default",
        ]
    )


def _unique_strings(values: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value not in result:
            result.append(value)
    return result


def _normalize_playback_metadata(
    playback_key: str,
    metadata: PlaybackMetadata | None,
) -> PlaybackMetadata:
    metadata = metadata or PlaybackMetadata()
    if metadata.playback_key:
        return metadata
    return PlaybackMetadata(
        playback_key=playback_key,
        run_id=metadata.run_id,
        interaction_session_id=metadata.interaction_session_id,
        workflow=metadata.workflow,
    )


def _merge_playback_metadata(
    current: PlaybackMetadata,
    update: PlaybackMetadata,
) -> PlaybackMetadata:
    return PlaybackMetadata(
        playback_key=current.playback_key or update.playback_key,
        run_id=current.run_id or update.run_id,
        interaction_session_id=(
            current.interaction_session_id or update.interaction_session_id
        ),
        workflow=current.workflow or update.workflow,
    )


def _can_report_playback_result(metadata: PlaybackMetadata | None) -> bool:
    return bool(metadata and metadata.playback_key and metadata.run_id)
