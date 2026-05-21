from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Callable

from .session import AutoVoiceSession
from .types import (
    AutoVoiceConfig,
    AutoVoiceMode,
    AutoVoiceSnapshot,
    RobotAudioSource,
    StreamHookFactory,
)


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


