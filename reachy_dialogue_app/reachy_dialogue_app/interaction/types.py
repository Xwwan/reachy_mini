from __future__ import annotations

from typing import Any, Literal, TypeAlias

JsonDict: TypeAlias = dict[str, Any]
Workflow: TypeAlias = Literal["chat", "onboarding"]
InputMode: TypeAlias = Literal["text", "local", "robot", "auto"]
AudioFormat: TypeAlias = Literal["pcm"]
