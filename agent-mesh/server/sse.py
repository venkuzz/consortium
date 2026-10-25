"""
SSE hub — Agent A (WORK_SPLIT.md §4.4).

Fan-out of server-sent events to every connected browser. Each subscriber is a
thread-safe queue; the store calls `hub.broadcast(event, data)` and every open
/api/events connection drains its queue to the client.

Decoupled from the store: the store is constructed with hub.broadcast as its
callback, so core/ never imports server/.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Optional


class Subscriber:
    def __init__(self, maxsize: int = 1000):
        self.q: "queue.Queue[str]" = queue.Queue(maxsize=maxsize)
        self.alive = True

    def put(self, chunk: str) -> None:
        try:
            self.q.put_nowait(chunk)
        except queue.Full:
            # Slow client: drop it rather than block the whole hub.
            self.alive = False

    def get(self, timeout: float) -> Optional[str]:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


class SSEHub:
    def __init__(self):
        self._subs: set[Subscriber] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs.discard(sub)

    def broadcast(self, event: str, data: dict) -> None:
        """Format one SSE frame and push to all subscribers."""
        chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        with self._lock:
            dead = []
            for sub in self._subs:
                sub.put(chunk)
                if not sub.alive:
                    dead.append(sub)
            for sub in dead:
                self._subs.discard(sub)

    def count(self) -> int:
        with self._lock:
            return len(self._subs)
