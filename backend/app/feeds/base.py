"""Common behaviour for broker feeds: connection state, stats and reconnect with backoff."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Callable

from ..models import BROKER_NAMES, Broker, Quote, now_ms

log = logging.getLogger("feeds")

QuoteHandler = Callable[[Quote], None]


class Feed:
    def __init__(self, broker: Broker):
        self.broker = broker
        self.state = "disconnected"       # connecting | connected | reconnecting | disconnected | error
        self.messages = 0
        self.reconnects = 0
        self.last_message_ts: float | None = None
        self.last_error: str | None = None
        self._latencies: list[float] = []
        self._stop = asyncio.Event()

    # -- to implement ---------------------------------------------------
    async def connect_and_stream(self, on_quote: QuoteHandler) -> None:
        """Connect and deliver quotes until the connection drops (then return or raise)."""
        raise NotImplementedError

    # -- shared logic ---------------------------------------------------
    def _emit(self, on_quote: QuoteHandler, quote: Quote) -> None:
        self.messages += 1
        self.last_message_ts = now_ms()
        self._latencies.append(quote.latency_ms)
        if len(self._latencies) > 500:
            del self._latencies[:250]
        on_quote(quote)

    async def run_forever(self, on_quote: QuoteHandler) -> None:
        """Keep the feed alive: reconnect with exponential backoff + jitter."""
        backoff = 1.0
        while not self._stop.is_set():
            self.state = "connecting" if self.messages == 0 else "reconnecting"
            try:
                await self.connect_and_stream(on_quote)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors, auth errors, ...
                self.last_error = str(exc)
                self.state = "error"
                log.warning("%s feed error: %s", self.broker.value, exc)
            if self._stop.is_set():
                break
            self.reconnects += 1
            self.state = "reconnecting"
            await asyncio.sleep(backoff + random.uniform(0, 0.5))
            backoff = min(backoff * 2, 30.0)
        self.state = "disconnected"

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        lat = sorted(self._latencies)
        return {
            "broker": self.broker.value,
            "name": BROKER_NAMES[self.broker],
            "state": self.state,
            "messages": self.messages,
            "reconnects": self.reconnects,
            "last_message_age_ms": round(now_ms() - self.last_message_ts) if self.last_message_ts else None,
            "latency_p50_ms": round(lat[len(lat) // 2], 1) if lat else None,
            "latency_p95_ms": round(lat[int(len(lat) * 0.95)], 1) if lat else None,
            "last_error": self.last_error,
        }
