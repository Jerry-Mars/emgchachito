"""Small reusable bridge from producer queues into synchronous handlers."""

from __future__ import annotations

import queue
from collections.abc import Callable
from typing import Generic, TypeVar


T = TypeVar("T")


class QueuePump(Generic[T]):
    """Drain a producer queue without knowing anything about record semantics."""

    def __init__(self, source: queue.Queue[T], handler: Callable[[T], None]) -> None:
        self.source = source
        self.handler = handler

    def drain(self, max_items: int = 1024) -> int:
        max_items = max(1, int(max_items))
        consumed = 0

        while consumed < max_items:
            try:
                item = self.source.get_nowait()
            except queue.Empty:
                break

            self.handler(item)
            consumed += 1

        return consumed
