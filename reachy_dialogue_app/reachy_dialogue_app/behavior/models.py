"""行为触发相关的数据模型。

大模型回复中可以嵌入形如 [emo:开心]、[act:wave] 的标签，本模块定义解析后
和触发后的结构化结果。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BehaviorTag:
    """从回复文本中解析出的单个行为标签。"""

    module: str
    tag_name: str
    key: str
    raw: str


@dataclass
class BehaviorTriggerResult:
    """一次行为触发尝试的结果，既用于调试，也会透传给前端事件流。"""

    matched: bool
    module: str | None = None
    tag_name: str | None = None
    key: str | None = None
    url: str | None = None
    triggered: bool = False
    ok: bool = False
    status_code: int | None = None
    error: str | None = None
