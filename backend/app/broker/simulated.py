"""Simulated broker: a virtual trading account plus an order management system.

Everything here is in-memory. There is no network call and no real broker API:
orders are matched against the simulated market's quotes after a simulated latency.
"""
from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field

from ..models import BROKER_NAMES, Broker, Exchange, now_ms
from ..engine.market import MarketDataEngine

ORDER_LATENCY_MS = {Broker.ANGEL: (40, 130), Broker.ZERODHA: (30, 100)}
LIMIT_TOLERANCE = 0.05   # a limit order still fills up to 1 tick worse than its price
MARGIN_RATE = 0.20       # intraday (MIS) margin: 20% of order value, i.e. 5x leverage
CLIENT_PREFIX = {Broker.ANGEL: "SIM-AO", Broker.ZERODHA: "SIM-ZD"}


@dataclass
class Order:
    order_id: str
    broker: Broker
    exchange: Exchange
    symbol: str
    side: str                    # BUY | SELL
    quantity: int
    order_type: str              # LIMIT | MARKET
    price: float | None          # limit price (None for MARKET)
    tag: str = ""                # trade id this order belongs to
    validity: str = "IOC"        # immediate-or-cancel: fill now or cancel
    status: str = "OPEN"         # OPEN -> COMPLETE | CANCELLED | REJECTED
    filled_qty: int = 0
    fill_price: float | None = None
    reason: str = ""
    latency_ms: float = 0.0
    placed_ts: float = field(default_factory=now_ms)
    updated_ts: float = field(default_factory=now_ms)

    def to_dict(self) -> dict:
        return {"order_id": self.order_id, "broker": self.broker.value, "exchange": self.exchange.value,
                "symbol": self.symbol, "side": self.side, "quantity": self.quantity, "order_type": self.order_type,
                "price": self.price, "validity": self.validity, "tag": self.tag, "status": self.status,
                "filled_qty": self.filled_qty, "fill_price": self.fill_price, "reason": self.reason,
                "latency_ms": round(self.latency_ms, 1), "placed_ts": self.placed_ts, "updated_ts": self.updated_ts}


@dataclass
class Account:
    broker: Broker
    client_id: str
    opening_balance: float
    realized_pnl: float = 0.0    # gross trading P&L booked to this account
    charges: float = 0.0         # brokerage, taxes and fees paid from this account
    blocked: dict[str, float] = field(default_factory=dict)   # margin held per order / open position

    @property
    def balance(self) -> float:
        return self.opening_balance + self.realized_pnl - self.charges

    @property
    def used_margin(self) -> float:
        return sum(self.blocked.values())

    @property
    def available_margin(self) -> float:
        return self.balance - self.used_margin

    def to_dict(self) -> dict:
        return {"broker": self.broker.value, "name": BROKER_NAMES[self.broker], "client_id": self.client_id,
                "account_type": "SIMULATED", "opening_balance": round(self.opening_balance, 2),
                "realized_pnl": round(self.realized_pnl, 2), "charges": round(self.charges, 2),
                "balance": round(self.balance, 2), "used_margin": round(self.used_margin, 2),
                "available_margin": round(self.available_margin, 2)}


class SimulatedBroker:
    """One virtual broker account. Orders fill against the simulated market only."""

    def __init__(self, broker: Broker, market: MarketDataEngine, capital: float,
                 rng: random.Random | None = None, competition: float = 0.0, on_order=None):
        self.broker = broker
        self.market = market
        self.rng = rng or random.Random()
        self.competition = competition      # chance another trader takes the liquidity first
        self.on_order = on_order
        self.account = Account(broker, f"{CLIENT_PREFIX[broker]}-{self.rng.randint(1000, 9999)}", capital)
        self.orders: deque[Order] = deque(maxlen=1000)
        self._seq = 0

    @staticmethod
    def margin_for(price: float, quantity: int) -> float:
        return price * quantity * MARGIN_RATE

    def _next_id(self) -> str:
        self._seq += 1
        return f"{CLIENT_PREFIX[self.broker]}-{self._seq:06d}"

    def _finish(self, order: Order, status: str, reason: str = "") -> Order:
        order.status, order.reason, order.updated_ts = status, reason, now_ms()
        self.account.blocked.pop(order.order_id, None)
        if self.on_order:
            self.on_order(order)
        return order

    async def place_order(self, symbol: str, exchange: Exchange, side: str, quantity: int,
                          order_type: str = "LIMIT", price: float | None = None, tag: str = "") -> Order:
        order = Order(self._next_id(), self.broker, exchange, symbol, side, quantity, order_type, price, tag)
        self.orders.appendleft(order)

        if quantity <= 0:
            return self._finish(order, "REJECTED", "Quantity must be positive")
        if order_type == "LIMIT" and price is None:
            return self._finish(order, "REJECTED", "Limit order needs a price")
        ref = self.market.quote(self.broker, exchange, symbol)
        ref_price = price if price is not None else (ref.ask if ref and side == "BUY" else ref.bid if ref else None)
        if ref_price is None:
            return self._finish(order, "REJECTED", "No market data for instrument")
        margin = self.margin_for(ref_price, quantity)
        if margin > self.account.available_margin:
            return self._finish(order, "REJECTED", f"Insufficient margin: need ₹{margin:,.0f}")
        self.account.blocked[order.order_id] = margin

        # simulated round trip to the exchange
        order.latency_ms = self.rng.uniform(*ORDER_LATENCY_MS[self.broker])
        await asyncio.sleep(order.latency_ms / 1000)

        q = self.market.quote(self.broker, exchange, symbol)
        if q is None:
            return self._finish(order, "CANCELLED", "IOC: no quote at exchange")
        fill = q.ask if side == "BUY" else q.bid
        depth = q.ask_qty if side == "BUY" else q.bid_qty
        if depth < 1:
            return self._finish(order, "CANCELLED", "IOC: no liquidity")
        if order_type == "LIMIT":
            worse_by = (fill - price) if side == "BUY" else (price - fill)
            if worse_by > LIMIT_TOLERANCE + 1e-9:
                return self._finish(order, "CANCELLED", f"IOC: price moved to ₹{fill:.2f}")
            if self.rng.random() < self.competition:
                return self._finish(order, "CANCELLED", "IOC: liquidity taken by another trader")
        order.fill_price, order.filled_qty = fill, quantity
        return self._finish(order, "COMPLETE")

    # ---- account bookkeeping ----
    def hold_margin(self, key: str, amount: float) -> None:
        self.account.blocked[key] = amount

    def release_margin(self, key: str) -> None:
        self.account.blocked.pop(key, None)

    def book(self, gross_pnl: float, charges: float) -> None:
        self.account.realized_pnl += gross_pnl
        self.account.charges += charges

    def reset(self) -> None:
        self.account.realized_pnl = self.account.charges = 0.0
        self.account.blocked.clear()
        self.orders.clear()
