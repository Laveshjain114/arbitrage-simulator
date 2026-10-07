"""Realistic market simulator: the only data source. No real exchange or broker is contacted.

One 'true' price per stock follows a random walk. NSE and BSE each quote around it
with their own spreads, depth and small mean-reverting price offsets. Occasionally
one exchange dislocates for a second or two - that is where real NSE/BSE arbitrage
opportunities come from. Each simulated broker receives the same exchange quotes
with its own network latency and occasional disconnects (to exercise reconnect logic).
"""
from __future__ import annotations

import asyncio
import heapq
import math
import os
import random
from dataclasses import dataclass, field

from ..models import Broker, Exchange, Quote, now_ms
from .base import Feed, QuoteHandler

TICK = 0.05


def round_tick(x: float) -> float:
    return round(round(x / TICK) * TICK, 2)


@dataclass
class _SymbolState:
    price: float
    offsets: dict = field(default_factory=lambda: {Exchange.NSE: 0.0, Exchange.BSE: 0.0})


class MarketSimulator:
    """Generates exchange-level top-of-book quotes and fans them out to broker feeds."""

    def __init__(self, instruments: dict[str, float], tick_interval_ms: int = 250, seed: int | None = None):
        self.rng = random.Random(seed)
        self.tick_s = tick_interval_ms / 1000
        self.state = {s: _SymbolState(price=p) for s, p in instruments.items()}
        self.subscribers: list["SimulatedBrokerFeed"] = []
        self.vol = 0.00008               # per-tick volatility of the true price
        self.dislocation_prob = 0.0025   # per symbol per exchange per tick
        self.decay = 0.2                 # how fast offsets revert (competing arbitrageurs close gaps)

    def subscribe(self, feed: "SimulatedBrokerFeed") -> None:
        self.subscribers.append(feed)

    def step(self) -> list[Quote]:
        ts = now_ms()
        quotes = []
        for symbol, st in self.state.items():
            st.price *= math.exp(self.rng.gauss(0, self.vol))
            for ex in (Exchange.NSE, Exchange.BSE):
                off = st.offsets[ex] * (1 - self.decay) + self.rng.gauss(0, st.price * 0.00002)
                if self.rng.random() < self.dislocation_prob:
                    off += self.rng.choice((-1, 1)) * st.price * self.rng.uniform(0.0003, 0.0016)
                st.offsets[ex] = off
                mid = st.price + off
                spread_ticks = self.rng.choice((1, 1, 2)) if ex is Exchange.NSE else self.rng.choice((1, 2, 2, 3, 4))
                bid = round_tick(mid - spread_ticks * TICK / 2)
                ask = round(bid + spread_ticks * TICK, 2)
                if ex is Exchange.NSE:
                    bq, aq = self.rng.randint(50, 2500), self.rng.randint(50, 2500)
                else:
                    bq, aq = self.rng.randint(10, 500), self.rng.randint(10, 500)
                quotes.append((symbol, ex, bid, ask, bq, aq, ts))
        out = []
        for feed in self.subscribers:
            for symbol, ex, bid, ask, bq, aq, t in quotes:
                q = Quote(feed.broker, ex, symbol, bid, ask, bq, aq, exchange_ts=t, received_ts=t)
                feed.enqueue(q)
                out.append(q)
        return out

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.step()
            await asyncio.sleep(self.tick_s)


class SimulatedBrokerFeed(Feed):
    """A broker connection with realistic latency jitter and occasional drops."""

    def __init__(self, broker: Broker, market: MarketSimulator, latency_ms: tuple[float, float],
                 disconnects: bool | None = None, seed: int | None = None):
        super().__init__(broker)
        self.rng = random.Random(seed)
        self.latency_ms = latency_ms
        self.disconnects = os.environ.get("SIM_DISCONNECTS", "1") == "1" if disconnects is None else disconnects
        self._pending: list[tuple[float, int, Quote]] = []
        self._seq = 0
        self._connected = False
        market.subscribe(self)

    def enqueue(self, q: Quote) -> None:
        if not self._connected:
            return  # quotes published while disconnected are lost, like a real socket
        lat = self.rng.uniform(*self.latency_ms)
        if self.rng.random() < 0.02:
            lat *= self.rng.uniform(3, 8)  # occasional network spike
        q.received_ts = q.exchange_ts + lat
        self._seq += 1
        heapq.heappush(self._pending, (q.received_ts, self._seq, q))

    async def connect_and_stream(self, on_quote: QuoteHandler) -> None:
        await asyncio.sleep(self.rng.uniform(0.2, 0.8))       # handshake
        self._connected = True
        self.state = "connected"
        session_s = self.rng.uniform(120, 300) if self.disconnects else math.inf
        started = now_ms()
        try:
            while not self._stop.is_set():
                t = now_ms()
                while self._pending and self._pending[0][0] <= t:
                    _, _, q = heapq.heappop(self._pending)
                    self._emit(on_quote, q)
                if (t - started) / 1000 > session_s:
                    raise ConnectionError("simulated connection drop")
                await asyncio.sleep(0.01)
        finally:
            self._connected = False
            self._pending.clear()
