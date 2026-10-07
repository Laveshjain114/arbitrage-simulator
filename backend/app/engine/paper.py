"""Paper execution: sends both legs of an arbitrage as simulated orders to two simulated
broker accounts, with realistic latency, slippage and 'legging risk' (one leg fills,
the other doesn't). Nothing here talks to a real broker."""
from __future__ import annotations

import asyncio
import os
import random
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from ..broker.simulated import SimulatedBroker
from ..config import RiskLimits
from ..models import Broker, Exchange, Opportunity, now_ms
from .costs import arbitrage_costs
from .market import MarketDataEngine

UNWIND_DELAY_S = (2.0, 4.0)  # how long a one-legged position stays open before being closed


@dataclass
class Leg:
    side: str                  # BUY | SELL
    exchange: Exchange
    broker: Broker
    limit: float
    quantity: int
    fill_price: float | None = None
    status: str = "PENDING"    # FILLED | MISSED
    latency_ms: float = 0.0
    order_id: str | None = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {"side": self.side, "exchange": self.exchange.value, "broker": self.broker.value,
                "limit": self.limit, "quantity": self.quantity, "fill_price": self.fill_price,
                "status": self.status, "latency_ms": round(self.latency_ms, 1),
                "order_id": self.order_id, "reason": self.reason}


@dataclass
class PaperTrade:
    id: str
    opportunity_id: str
    symbol: str
    quantity: int
    expected_net: float
    buy: Leg
    sell: Leg
    status: str = "PENDING"    # COMPLETED | LEGGED | MISSED
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    slippage: float = 0.0
    latency_ms: float = 0.0
    note: str = ""
    ts: float = field(default_factory=now_ms)

    def to_dict(self) -> dict:
        return {"id": self.id, "opportunity_id": self.opportunity_id, "symbol": self.symbol,
                "quantity": self.quantity, "status": self.status, "expected_net": round(self.expected_net, 2),
                "gross_pnl": round(self.gross_pnl, 2), "costs": round(self.costs, 2),
                "net_pnl": round(self.net_pnl, 2), "slippage": round(self.slippage, 2),
                "latency_ms": round(self.latency_ms, 1), "note": self.note, "ts": self.ts,
                "buy": self.buy.to_dict(), "sell": self.sell.to_dict()}


@dataclass
class OpenPosition:
    trade_id: str
    symbol: str
    side: str          # LONG | SHORT
    exchange: Exchange
    broker: Broker
    quantity: int
    entry: float
    opened_ts: float = field(default_factory=now_ms)


class PaperTradingEngine:
    def __init__(self, market: MarketDataEngine, risk: RiskLimits, starting_capital: float,
                 on_trade=None, seed: int | None = None, on_order=None):
        self.market = market
        self.risk = risk
        self.rng = random.Random(seed)
        self.starting_capital = starting_capital
        self.enabled = True
        self.halted_reason: str | None = None
        self.trades: deque[PaperTrade] = deque(maxlen=500)
        self.positions: dict[str, OpenPosition] = {}
        self.realized_pnl = 0.0
        self.total_costs = 0.0
        self.pnl_series: deque[tuple[float, float]] = deque(maxlen=600)
        self._recent_trade_ts: deque[float] = deque()
        self._last_trade_by_symbol: dict[str, float] = {}
        self._inflight: set[str] = set()
        self.rejections: dict[str, int] = {}
        self.on_trade = on_trade
        # chance that a leg loses the race for liquidity to another trader (0 in tests)
        self.competition = float(os.environ.get("FILL_COMPETITION", "0.12")) if seed is None else 0.0
        self._day = datetime.now().date()
        self._day_start_pnl = 0.0
        # one simulated account per broker, capital split equally
        self.brokers = {b: SimulatedBroker(b, market, starting_capital / 2, self.rng, self.competition, on_order)
                        for b in Broker}

    # ---------------- risk checks ----------------
    def _reject(self, reason: str) -> bool:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        return False

    def can_trade(self, opp: Opportunity) -> bool:
        t = now_ms()
        if not self.enabled:
            return False
        if self.halted_reason:
            return self._reject("halted")
        if opp.net_pnl < self.risk.min_net_pnl:
            return False  # not worth trading - counted by the detector, not a rejection
        if opp.symbol in self._inflight:
            return self._reject("order in flight")
        if t - self._last_trade_by_symbol.get(opp.symbol, 0) < self.risk.symbol_cooldown_ms:
            return self._reject("symbol cooldown")
        while self._recent_trade_ts and t - self._recent_trade_ts[0] > 60_000:
            self._recent_trade_ts.popleft()
        if len(self._recent_trade_ts) >= self.risk.max_trades_per_minute:
            return self._reject("max trades/minute")
        if opp.buy_price * opp.quantity > self.risk.max_notional:
            return self._reject("max notional")
        need = {b: 0.0 for b in Broker}
        need[opp.buy_broker] += SimulatedBroker.margin_for(opp.buy_price, opp.quantity)
        need[opp.sell_broker] += SimulatedBroker.margin_for(opp.sell_price, opp.quantity)
        if any(amount > self.brokers[b].account.available_margin for b, amount in need.items()):
            return self._reject("insufficient margin")
        return True

    def _check_daily_loss(self) -> None:
        today = datetime.now().date()
        if today != self._day:
            self._day, self._day_start_pnl, self.halted_reason = today, self.realized_pnl, None
        if self.realized_pnl - self._day_start_pnl <= -self.risk.daily_loss_limit:
            self.halted_reason = f"Daily loss limit ₹{self.risk.daily_loss_limit:,.0f} reached"

    # ---------------- execution ----------------
    def submit(self, opp: Opportunity) -> None:
        if not self.can_trade(opp):
            return
        t = now_ms()
        self._inflight.add(opp.symbol)
        self._last_trade_by_symbol[opp.symbol] = t
        self._recent_trade_ts.append(t)
        asyncio.get_running_loop().create_task(self._execute(opp))

    async def _fill_leg(self, leg: Leg, symbol: str, tag: str) -> None:
        order = await self.brokers[leg.broker].place_order(
            symbol, leg.exchange, leg.side, leg.quantity, "LIMIT", leg.limit, tag)
        leg.order_id, leg.latency_ms, leg.reason = order.order_id, order.latency_ms, order.reason
        leg.status = "FILLED" if order.status == "COMPLETE" else "MISSED"
        leg.fill_price = order.fill_price

    async def _execute(self, opp: Opportunity) -> None:
        started = now_ms()
        trade = PaperTrade(
            id=uuid.uuid4().hex[:10], opportunity_id=opp.id, symbol=opp.symbol, quantity=opp.quantity,
            expected_net=opp.net_pnl,
            buy=Leg("BUY", opp.buy_exchange, opp.buy_broker, opp.buy_price, opp.quantity),
            sell=Leg("SELL", opp.sell_exchange, opp.sell_broker, opp.sell_price, opp.quantity),
        )
        try:
            await asyncio.gather(self._fill_leg(trade.buy, opp.symbol, trade.id),
                                 self._fill_leg(trade.sell, opp.symbol, trade.id))
            trade.latency_ms = now_ms() - started   # signal-to-fill time for both legs

            if trade.buy.status == "FILLED" and trade.sell.status == "FILLED":
                trade.status = "COMPLETED"
                costs = arbitrage_costs(trade.buy.broker, trade.buy.exchange, trade.buy.fill_price,
                                        trade.sell.broker, trade.sell.exchange, trade.sell.fill_price, opp.quantity)
                trade.gross_pnl = (trade.sell.fill_price - trade.buy.fill_price) * opp.quantity
                trade.costs = costs.total
                trade.net_pnl = trade.gross_pnl - trade.costs
                trade.slippage = ((trade.buy.fill_price - opp.buy_price) + (opp.sell_price - trade.sell.fill_price)) * opp.quantity
                # each account books half the spread and pays the charges of its own leg (approx. half)
                for b in (trade.buy.broker, trade.sell.broker):
                    self.brokers[b].book(trade.gross_pnl / 2, trade.costs / 2)
            elif trade.buy.status == "MISSED" and trade.sell.status == "MISSED":
                trade.status = "MISSED"
                trade.note = "Spread closed before either leg filled"
            else:
                # Legging risk: one side filled. Hold briefly (unrealised P&L) then unwind at market.
                trade.status = "LEGGED"
                filled = trade.buy if trade.buy.status == "FILLED" else trade.sell
                pos = OpenPosition(trade.id, opp.symbol, "LONG" if filled.side == "BUY" else "SHORT",
                                   filled.exchange, filled.broker, opp.quantity, filled.fill_price)
                self.positions[trade.id] = pos
                broker = self.brokers[pos.broker]
                broker.hold_margin(trade.id, SimulatedBroker.margin_for(pos.entry, pos.quantity))
                await asyncio.sleep(self.rng.uniform(*UNWIND_DELAY_S))
                # square off with a simulated market order on the same account
                exit_order = await broker.place_order(opp.symbol, pos.exchange, "SELL" if pos.side == "LONG" else "BUY",
                                                      pos.quantity, "MARKET", tag=trade.id)
                exit_price = exit_order.fill_price if exit_order.fill_price is not None else self._mark(pos)
                broker.release_margin(trade.id)
                self.positions.pop(trade.id, None)
                per_share = (exit_price - pos.entry) if pos.side == "LONG" else (pos.entry - exit_price)
                trade.gross_pnl = per_share * opp.quantity
                buy_px, sell_px = (pos.entry, exit_price) if pos.side == "LONG" else (exit_price, pos.entry)
                costs = arbitrage_costs(pos.broker, pos.exchange, buy_px, pos.broker, pos.exchange, sell_px, opp.quantity)
                trade.costs = costs.total
                trade.net_pnl = trade.gross_pnl - trade.costs
                broker.book(trade.gross_pnl, trade.costs)
                trade.note = f"{'Sell' if pos.side == 'LONG' else 'Buy'} leg missed; position unwound on {pos.exchange.value} at ₹{exit_price:.2f}"

            self.realized_pnl += trade.net_pnl
            self.total_costs += trade.costs
            self.pnl_series.append((now_ms(), round(self.realized_pnl, 2)))
            self._check_daily_loss()
            self.trades.appendleft(trade)
            if self.on_trade:
                self.on_trade(trade)
        finally:
            self._inflight.discard(opp.symbol)

    # ---------------- portfolio ----------------
    def _mark(self, pos: OpenPosition) -> float:
        q = self.market.quote(pos.broker, pos.exchange, pos.symbol)
        if q is None:
            return pos.entry
        return q.bid if pos.side == "LONG" else q.ask

    def unrealized_pnl(self) -> float:
        total = 0.0
        for p in self.positions.values():
            px = self._mark(p)
            total += (px - p.entry) * p.quantity if p.side == "LONG" else (p.entry - px) * p.quantity
        return total

    def portfolio(self) -> dict:
        unreal = self.unrealized_pnl()
        return {
            "starting_capital": self.starting_capital,
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(unreal, 2),
            "equity": round(self.starting_capital + self.realized_pnl + unreal, 2),
            "total_costs": round(self.total_costs, 2),
            "accounts": self.accounts(),
            "open_positions": [{"trade_id": p.trade_id, "symbol": p.symbol, "side": p.side,
                                "exchange": p.exchange.value, "broker": p.broker.value,
                                "quantity": p.quantity, "entry": p.entry, "mark": self._mark(p)}
                               for p in self.positions.values()],
        }

    def accounts(self) -> list[dict]:
        return [b.account.to_dict() | {"orders": len(b.orders)} for b in self.brokers.values()]

    def orders(self, limit: int = 100, broker: str | None = None, status: str | None = None,
               symbol: str | None = None) -> list[dict]:
        out = [o for b in self.brokers.values() for o in b.orders
               if (broker is None or o.broker.value == broker) and (status is None or o.status == status)
               and (symbol is None or o.symbol == symbol)]
        out.sort(key=lambda o: o.placed_ts, reverse=True)
        return [o.to_dict() for o in out[:limit]]

    def order_stats(self) -> dict:
        counts: dict[str, int] = {}
        for b in self.brokers.values():
            for o in b.orders:
                counts[o.status] = counts.get(o.status, 0) + 1
        return counts

    def analytics(self) -> dict:
        trades = list(self.trades)
        done = [t for t in trades if t.status in ("COMPLETED", "LEGGED")]
        wins = [t for t in done if t.net_pnl > 0]
        lat = sorted(t.latency_ms for t in done) or [0]
        by_symbol: dict[str, dict] = {}
        for t in done:
            s = by_symbol.setdefault(t.symbol, {"symbol": t.symbol, "trades": 0, "net_pnl": 0.0})
            s["trades"] += 1
            s["net_pnl"] = round(s["net_pnl"] + t.net_pnl, 2)
        return {
            "trades": len(trades),
            "completed": sum(t.status == "COMPLETED" for t in trades),
            "legged": sum(t.status == "LEGGED" for t in trades),
            "missed": sum(t.status == "MISSED" for t in trades),
            "win_rate": round(100 * len(wins) / len(done), 1) if done else 0.0,
            "avg_net_pnl": round(sum(t.net_pnl for t in done) / len(done), 2) if done else 0.0,
            "avg_slippage": round(sum(t.slippage for t in done) / len(done), 2) if done else 0.0,
            "latency_p50_ms": round(lat[len(lat) // 2], 1),
            "latency_p95_ms": round(lat[int(len(lat) * 0.95)], 1),
            "by_symbol": sorted(by_symbol.values(), key=lambda s: -s["net_pnl"]),
            "rejections": self.rejections,
            "pnl_series": [{"t": t, "pnl": p} for t, p in self.pnl_series],
        }

    def reset(self) -> None:
        self.trades.clear()
        self.positions.clear()
        self.realized_pnl = self.total_costs = self._day_start_pnl = 0.0
        self.pnl_series.clear()
        self.rejections.clear()
        self.halted_reason = None
        for b in self.brokers.values():
            b.reset()
