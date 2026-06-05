from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import quote

import requests

from .models import BehaviorTag, BehaviorTriggerResult

TAG_PATTERN = re.compile(r"\[([^:\]\r\n]+):([^\]\r\n]+)\]")


class BehaviorTriggerTracker:
    """Trigger behavior tags once while a streamed reply is being assembled."""

    def __init__(self, config: dict[str, Any] | None) -> None:
        self.config = config
        self.buffer = ""
        self.triggered_tag_counts: Counter[tuple[str, str, str]] = Counter()

    def trigger_from_fragment(self, fragment: str) -> list[BehaviorTriggerResult]:
        if fragment:
            self.buffer += fragment
        return self.trigger_from_text(self.buffer)

    def trigger_from_text(self, text: str) -> list[BehaviorTriggerResult]:
        if not self.config or not self.config.get("enabled", True):
            return []

        seen_in_text: Counter[tuple[str, str, str]] = Counter()
        results: list[BehaviorTriggerResult] = []
        for tag in _extract_behavior_tags(text, self.config):
            tag_key = (tag.module, tag.tag_name.casefold(), tag.key)
            occurrence_index = seen_in_text[tag_key]
            seen_in_text[tag_key] += 1
            if occurrence_index < self.triggered_tag_counts[tag_key]:
                continue
            results.append(_trigger_behavior_tag(tag, self.config))
            self.triggered_tag_counts[tag_key] += 1
        return results


def _trigger_behaviors_from_text(
    text: str,
    config: dict[str, Any] | None,
) -> list[BehaviorTriggerResult]:
    if not config or not config.get("enabled", True):
        return []

    return [
        _trigger_behavior_tag(tag, config)
        for tag in _extract_behavior_tags(text, config)
    ]


def _extract_behavior_tags(
    text: str,
    config: dict[str, Any] | None,
) -> list[BehaviorTag]:
    if not text or not config:
        return []
    tag_to_module: dict[str, str] = {}
    modules = config.get("modules") or {}
    for module_name, module_config in modules.items():
        if not isinstance(module_config, dict) or not module_config.get("enabled", True):
            continue
        for tag_name in module_config.get("tag_names") or []:
            tag_to_module.setdefault(str(tag_name).casefold(), str(module_name))

    tags: list[BehaviorTag] = []
    for match in TAG_PATTERN.finditer(text):
        tag_name = match.group(1).strip()
        key = match.group(2).strip()
        if not tag_name or not key:
            continue
        module = tag_to_module.get(tag_name.casefold())
        if module is None:
            continue
        tags.append(
            BehaviorTag(
                module=module,
                tag_name=tag_name,
                key=key,
                raw=match.group(0),
            )
        )
    return tags


def _trigger_behavior_tag(
    tag: BehaviorTag,
    config: dict[str, Any],
) -> BehaviorTriggerResult:
    module_config = (config.get("modules") or {}).get(tag.module)
    if not isinstance(module_config, dict) or not module_config.get("enabled", True):
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            error="Module disabled",
        )
    if not _trigger_allowed(tag.key, module_config.get("triggers")):
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            error="Trigger key not configured",
        )

    if module_config.get("trigger_mode") == "function":
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            triggered=True,
            ok=True,
        )

    service_url = str(module_config.get("service_url") or "").rstrip("/")
    if not service_url:
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            error="Missing service_url",
        )

    endpoint = _render_endpoint(module_config, tag)
    url = _join_service_url(service_url, endpoint)
    method = str(module_config.get("method") or "GET").upper()
    timeout = float(module_config.get("request_timeout_seconds") or 3.0)
    try:
        if method == "POST":
            response = requests.post(
                url,
                json=_render_json_body(module_config, tag),
                timeout=timeout,
            )
        else:
            response = requests.get(url, timeout=timeout)
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            url=url,
            triggered=True,
            ok=response.ok,
            status_code=response.status_code,
            error=None if response.ok else response.text[:300],
        )
    except requests.RequestException as exc:
        return BehaviorTriggerResult(
            matched=True,
            module=tag.module,
            tag_name=tag.tag_name,
            key=tag.key,
            url=url,
            triggered=True,
            ok=False,
            error=str(exc),
        )


def _trigger_allowed(key: str, triggers: Any) -> bool:
    if triggers == "*":
        return True
    if isinstance(triggers, list):
        return key in triggers
    return False


def _render_endpoint(module_config: dict[str, Any], tag: BehaviorTag) -> str:
    template = str(module_config.get("endpoint_template") or "/{key}")
    return _render_template(template, tag, quote_key=True)


def _render_json_body(module_config: dict[str, Any], tag: BehaviorTag) -> Any:
    body = module_config.get("json_body")
    if body is None:
        body = {"key": "{key}"}
    return _render_template(body, tag, quote_key=False)


def _render_template(value: Any, tag: BehaviorTag, *, quote_key: bool) -> Any:
    replacements = {
        "module": tag.module,
        "tag": tag.tag_name,
        "key": quote(tag.key, safe="") if quote_key else tag.key,
        "raw": quote(tag.raw, safe="") if quote_key else tag.raw,
    }
    if isinstance(value, str):
        rendered = value
        for name, replacement in replacements.items():
            rendered = rendered.replace("{" + name + "}", replacement)
        return rendered
    if isinstance(value, list):
        return [_render_template(item, tag, quote_key=quote_key) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _render_template(item, tag, quote_key=quote_key)
            for key, item in value.items()
        }
    return value


def _join_service_url(service_url: str, endpoint: str) -> str:
    if not endpoint:
        endpoint = "/"
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return service_url.rstrip("/") + endpoint


def _first_module_result(
    results: list[BehaviorTriggerResult],
    module: str,
) -> BehaviorTriggerResult | None:
    for result in results:
        if result.module == module:
            return result
    return None


def _first_ok_module_key(
    results: list[BehaviorTriggerResult],
    module: str,
) -> str | None:
    result = _first_module_result(results, module)
    if result is None or not result.ok:
        return None
    return result.key


def _module_config(config: dict[str, Any], module: str) -> dict[str, Any] | None:
    module_config = (config.get("modules") or {}).get(module)
    if isinstance(module_config, dict):
        return dict(module_config)
    return None


def _disable_behavior_module(config: dict[str, Any], module: str) -> None:
    module_config = (config.get("modules") or {}).get(module)
    if isinstance(module_config, dict):
        module_config["enabled"] = False


def _behavior_result_payload(result: BehaviorTriggerResult) -> dict[str, Any]:
    return {
        "matched": result.matched,
        "module": result.module,
        "tag": result.tag_name,
        "key": result.key,
        "url": result.url,
        "triggered": result.triggered,
        "ok": result.ok,
        "status_code": result.status_code,
        "error": result.error,
    }


def _emoji_result_payload(result: BehaviorTriggerResult) -> dict[str, Any]:
    return {
        "matched": result.matched,
        "signal": result.key,
        "emotion": result.key,
        "url": result.url,
        "ok": result.ok,
        "status_code": result.status_code,
        "error": result.error,
    }
