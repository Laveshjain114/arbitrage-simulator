"""Detects NSE <-> BSE arbitrage: buy where the ask is lower, sell where the bid is higher."""
from __future__ import annotations

import uuid
from collections import deque

from ..config import RiskLimits
from ..models import Exchange, Opportunity, now_ms
from .costs import arbitrage_costs
from .market import MarketDataEngine


class ArbitrageDetector:
    def __init__(self, market: MarketDataEngine, risk: RiskLimits):
        self.market = market
        self.risk = risk
        self.active: dict[tuple[str, Exchange], Opportunity] = {}  # open episodes by (symbol, buy exchange)
        self.recent: deque[Opportunity] = deque(maxlen=50)          # closed + open episodes, newest first
        self.episodes = 0
        self.profitable_episodes = 0
        self.evaluations = 0

    def evaluate(self, symbol: str) -> Opportunity | None:
        """Return the best opportunity for a symbol right now (or None)."""
        self.evaluations += 1
        max_age = self.risk.max_quote_age_ms
        nse_bid, nse_ask = self.market.best(Exchange.NSE, symbol, max_age)
        bse_bid, bse_ask = self.market.best(Exchange.BSE, symbol, max_age)

        best: Opportunity | None = None
        for buy_ex, buy, sell_ex, sell in ((Exchange.NSE, nse_ask, Exchange.BSE, bse_bid),
                                           (Exchange.BSE, bse_ask, Exchange.NSE, nse_bid)):
            key = (symbol, buy_ex)
            if not buy or not sell or sell.price <= buy.price:
                self._close(key)
                continue
            qty = min(buy.qty, sell.qty, self.risk.max_quantity, int(self.risk.max_notional // buy.price))
            if qty <= 0:
                self._close(key)
                continue
            costs = arbitrage_costs(buy.broker, buy_ex, buy.price, sell.broker, sell_ex, sell.price, qty)
            opp = Opportunity(id=uuid.uuid4().hex[:10], symbol=symbol,
                              buy_exchange=buy_ex, buy_broker=buy.broker, buy_price=buy.price,
                              sell_exchange=sell_ex, sell_broker=sell.broker, sell_price=sell.price,
                              quantity=qty, gross_spread=round(sell.price - buy.price, 2), costs=costs)
            self._track(key, opp)
            if best is None or opp.net_pnl > best.net_pnl:
                best = opp
        return best

    # An "episode" is one continuous period where a spread stays open, so a spread that
    # lasts 2 seconds counts once, not once per tick.
    def _track(self, key, opp: Opportunity) -> None:
        cur = self.active.get(key)
        if cur is None:
            self.active[key] = opp
            self.episodes += 1
            if opp.profitable:
                self.profitable_episodes += 1
            self.recent.appendleft(opp)
        elif opp.net_pnl > cur.net_pnl:
            # keep the same episode id, update to the better quote
            opp.id, opp.detected_ts = cur.id, cur.detected_ts
            if opp.profitable and not cur.profitable:
                self.profitable_episodes += 1
            self.active[key] = opp
            for i, o in enumerate(self.recent):
                if o.id == cur.id:
                    self.recent[i] = opp
                    break

    def _close(self, key) -> None:
        self.active.pop(key, None)

    def stats(self) -> dict:
        return {
            "episodes": self.episodes,
            "profitable_episodes": self.profitable_episodes,
            "active": len(self.active),
            "evaluations": self.evaluations,
        }

    def snapshot(self) -> list[dict]:
        t = now_ms()
        out = []
        for o in list(self.recent)[:25]:
            d = o.to_dict()
            d["open"] = any(a.id == o.id for a in self.active.values())
            d["age_ms"] = round(t - o.detected_ts)
            out.append(d)
        return out
