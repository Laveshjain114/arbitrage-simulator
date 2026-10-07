"""Market data engine: keeps the latest quote from every broker feed and builds a
consolidated best bid / best ask per exchange."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..models import Broker, Exchange, Quote, now_ms


@dataclass(slots=True)
class BestSide:
    price: float
    qty: int
    broker: Broker
    age_ms: float


class MarketDataEngine:
    def __init__(self):
        self.book: dict[tuple[Broker, Exchange, str], Quote] = {}
        self.listeners: list[Callable[[Quote], None]] = []
        self.updates = 0

    def on_quote(self, q: Quote) -> None:
        key = (q.broker, q.exchange, q.symbol)
        prev = self.book.get(key)
        if prev and prev.exchange_ts > q.exchange_ts:
            return  # out-of-order tick, keep the newer one
        self.book[key] = q
        self.updates += 1
        for fn in self.listeners:
            fn(q)

    def quote(self, broker: Broker, exchange: Exchange, symbol: str) -> Quote | None:
        return self.book.get((broker, exchange, symbol))

    def best(self, exchange: Exchange, symbol: str, max_age_ms: float) -> tuple[BestSide | None, BestSide | None]:
        """Best bid and best ask for an exchange across all fresh broker quotes."""
        t = now_ms()
        best_bid = best_ask = None
        for broker in Broker:
            q = self.book.get((broker, exchange, symbol))
            if not q:
                continue
            age = t - q.received_ts
            if age > max_age_ms:
                continue
            if best_bid is None or q.bid > best_bid.price:
                best_bid = BestSide(q.bid, q.bid_qty, broker, age)
            if best_ask is None or q.ask < best_ask.price:
                best_ask = BestSide(q.ask, q.ask_qty, broker, age)
        return best_bid, best_ask

    def snapshot(self, symbols: list[str]) -> list[dict]:
        rows = []
        t = now_ms()
        for s in symbols:
            row = {"symbol": s}
            for b in Broker:
                for ex in Exchange:
                    q = self.book.get((b, ex, s))
                    row[f"{b.value}_{ex.value}"] = None if not q else {
                        "bid": q.bid, "ask": q.ask, "bid_qty": q.bid_qty, "ask_qty": q.ask_qty,
                        "age_ms": round(t - q.received_ts), "latency_ms": round(q.latency_ms, 1),
                    }
            rows.append(row)
        return rows
