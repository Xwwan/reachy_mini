"""行为触发子包导出。

对外只暴露 app 其他模块需要的配置加载、标签触发和 payload 转换函数。
"""

from .action import _play_action_signal
from .config import (
    _default_behavior_config,
    _load_behavior_config,
    _public_behavior_config,
    _public_emoji_config,
)
from .models import BehaviorTag, BehaviorTriggerResult
from .triggers import (
    BehaviorTriggerTracker,
    _behavior_result_payload,
    _disable_behavior_module,
    _emoji_result_payload,
    _extract_behavior_tags,
    _first_module_result,
    _first_ok_module_key,
    _module_config,
    _trigger_behavior_tag,
    _trigger_behaviors_from_text,
)

__all__ = [
    "BehaviorTag",
    "BehaviorTriggerTracker",
    "BehaviorTriggerResult",
    "_behavior_result_payload",
    "_default_behavior_config",
    "_disable_behavior_module",
    "_emoji_result_payload",
    "_extract_behavior_tags",
    "_first_module_result",
    "_first_ok_module_key",
    "_load_behavior_config",
    "_module_config",
    "_play_action_signal",
    "_public_behavior_config",
    "_public_emoji_config",
    "_trigger_behavior_tag",
    "_trigger_behaviors_from_text",
]
