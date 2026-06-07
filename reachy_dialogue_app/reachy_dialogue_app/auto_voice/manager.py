"""自动语音会话管理器。

Manager 负责创建、保存和停止 AutoVoiceSession。它不直接处理音频，而是把
服务地址、机器人麦克风读取函数和流式播放 hook 注入到每个 session 中。
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Callable

from ..interaction.client import InteractionApiClient
from .session import AutoVoiceSession
from .types import (
    AutoVoiceConfig,
    AutoVoiceMode,
    AutoVoiceSnapshot,
    RobotAudioSource,
    StreamHookFactory,
)


class AutoVoiceManager:
    """线程安全的自动语音 session registry。"""

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
        workflow: str = "chat",
    ) -> AutoVoiceSession:
        """创建新的自动语音 session，并先在 Interaction 服务侧开对话会话。"""

        session_id = f"auto_{uuid.uuid4().hex}"
        service_url = self.service_url_getter()
        # 自动语音最终仍走 Interaction API；这里先建立一条长生命周期的
        # interaction_session，后续每段语音都复用它来保留上下文。
        interaction_session = InteractionApiClient(service_url).create_session(
            workflow=workflow,  # type: ignore[arg-type]
            conversation_id=conversation_id,
            input_mode="auto",
            tts_enabled=tts_enabled,
        )
        interaction_session_id = interaction_session.get("interaction_session_id")
        if not isinstance(interaction_session_id, str) or not interaction_session_id:
            raise RuntimeError("Interaction session creation did not return an id.")
        session = AutoVoiceSession(
            session_id=session_id,
            mode=mode,
            service_url=service_url,
            conversation_id=conversation_id,
            interaction_session_id=interaction_session_id,
            workflow=str(interaction_session.get("workflow") or workflow),
            tts_enabled=tts_enabled,
            model_path=self.model_path,
            config=self.config,
            robot_audio_source=self.robot_audio_source,
            # hook 只按 session_id 构造一次，内部会维护播放分组和行为触发状态。
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
