from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BehaviorTag:
    module: str
    tag_name: str
    key: str
    raw: str


@dataclass
class BehaviorTriggerResult:
    matched: bool
    module: str | None = None
    tag_name: str | None = None
    key: str | None = None
    url: str | None = None
    triggered: bool = False
    ok: bool = False
    status_code: int | None = None
    error: str | None = None
