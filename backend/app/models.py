"""Core data types shared by feeds, the market data engine and the trading engine."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"


class Broker(str, Enum):
    ANGEL = "angelone"
    ZERODHA = "zerodha"


# Simulated brokers modelled on these brokers' charge sheets and latency - not connected to them
BROKER_NAMES = {Broker.ANGEL: "Angel One (Sim)", Broker.ZERODHA: "Zerodha (Sim)"}


def now_ms() -> float:
    return time.time() * 1000


@dataclass(slots=True)
class Quote:
    """Top of book for one instrument on one exchange, as seen by one broker feed."""
    broker: Broker
    exchange: Exchange
    symbol: str
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    exchange_ts: float          # when the exchange produced the quote (ms)
    received_ts: float = field(default_factory=now_ms)  # when our feed received it (ms)

    @property
    def latency_ms(self) -> float:
        return self.received_ts - self.exchange_ts

    def to_dict(self) -> dict:
        d = asdict(self)
        d["broker"] = self.broker.value
        d["exchange"] = self.exchange.value
        d["latency_ms"] = round(self.latency_ms, 1)
        return d


@dataclass(slots=True)
class CostBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp_duty: float
    gst: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_txn + self.sebi + self.stamp_duty + self.gst

    def to_dict(self) -> dict:
        d = {k: round(v, 2) for k, v in asdict(self).items()}
        d["total"] = round(self.total, 2)
        return d


@dataclass(slots=True)
class Opportunity:
    id: str
    symbol: str
    buy_exchange: Exchange
    buy_broker: Broker
    buy_price: float
    sell_exchange: Exchange
    sell_broker: Broker
    sell_price: float
    quantity: int
    gross_spread: float          # per share
    costs: CostBreakdown          # for the whole quantity, both legs
    detected_ts: float = field(default_factory=now_ms)

    @property
    def gross_pnl(self) -> float:
        return self.gross_spread * self.quantity

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.costs.total

    @property
    def net_spread(self) -> float:
        return self.net_pnl / self.quantity if self.quantity else 0.0

    @property
    def profitable(self) -> bool:
        return self.net_pnl > 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "buy": {"exchange": self.buy_exchange.value, "broker": self.buy_broker.value, "price": self.buy_price},
            "sell": {"exchange": self.sell_exchange.value, "broker": self.sell_broker.value, "price": self.sell_price},
            "quantity": self.quantity,
            "gross_spread": round(self.gross_spread, 2),
            "net_spread": round(self.net_spread, 4),
            "gross_pnl": round(self.gross_pnl, 2),
            "costs": self.costs.to_dict(),
            "net_pnl": round(self.net_pnl, 2),
            "profitable": self.profitable,
            "detected_ts": self.detected_ts,
        }
