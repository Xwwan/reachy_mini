from __future__ import annotations

import queue
import threading
from typing import Any, Callable
from urllib.parse import urljoin

import requests

from ..core.http import _iter_sse_events, _normalize_service_url


class FollowupStreamHub:
    def __init__(
        self,
        on_upstream_event: Callable[[tuple[str, str, bool], dict[str, Any]], None] | None = None,
    ) -> None:
        self.on_upstream_event = on_upstream_event
        self.lock = threading.Lock()
        self.subscribers: dict[tuple[str, str, bool], set[queue.Queue[dict]]] = {}
        self.threads: dict[tuple[str, str, bool], threading.Thread] = {}

    def subscribe(
        self,
        *,
        service_url: str,
        conversation_id: str,
        tts_enabled: bool,
    ):
        key = (_normalize_service_url(service_url), conversation_id, bool(tts_enabled))
        subscriber: queue.Queue[dict] = queue.Queue()
        with self.lock:
            subscribers = self.subscribers.setdefault(key, set())
            subscribers.add(subscriber)
            thread = self.threads.get(key)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(
                    target=self._run_upstream,
                    args=(key,),
                    name=f"followup-stream-{conversation_id}",
                    daemon=True,
                )
                self.threads[key] = thread
                thread.start()

        try:
            while True:
                try:
                    yield subscriber.get(timeout=15)
                except queue.Empty:
                    yield {
                        "event": "ping",
                        "data": {"conversation_id": conversation_id},
                    }
        finally:
            with self.lock:
                subscribers = self.subscribers.get(key)
                if subscribers is not None:
                    subscribers.discard(subscriber)
                    if not subscribers:
                        self.subscribers.pop(key, None)

    def _run_upstream(self, key: tuple[str, str, bool]) -> None:
        service_url, conversation_id, tts_enabled = key
        response: requests.Response | None = None
        try:
            response = requests.get(
                urljoin(service_url, "/followups/stream"),
                params={
                    "conversation_id": conversation_id,
                    "tts_enabled": str(tts_enabled).lower(),
                },
                stream=True,
                timeout=(3, None),
            )
            for item in _iter_sse_events(response):
                if self.on_upstream_event is not None:
                    self.on_upstream_event(key, item)
                self._publish(key, item)
                with self.lock:
                    if not self.subscribers.get(key):
                        return
        except Exception as exc:
            self._publish(
                key,
                {
                    "event": "followup_error",
                    "data": {"message": str(exc) or exc.__class__.__name__},
                },
            )
        finally:
            if response is not None:
                response.close()
            with self.lock:
                thread = self.threads.get(key)
                if thread is threading.current_thread():
                    self.threads.pop(key, None)

    def _publish(self, key: tuple[str, str, bool], item: dict) -> None:
        with self.lock:
            subscribers = list(self.subscribers.get(key) or [])
        for subscriber in subscribers:
            subscriber.put(item)
