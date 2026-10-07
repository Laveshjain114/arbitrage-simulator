"""Trade and order history persistence. SQLite locally, PostgreSQL in production (set DATABASE_URL)."""
from __future__ import annotations

import json
import logging

from sqlalchemy import JSON, Column, DateTime, Float, Integer, MetaData, String, Table, create_engine, func, insert, select

log = logging.getLogger("db")
metadata = MetaData()

trades = Table(
    "paper_trades", metadata,
    Column("id", String(16), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), index=True),
    Column("symbol", String(32), index=True),
    Column("status", String(16), index=True),
    Column("quantity", Integer),
    Column("expected_net", Float),
    Column("gross_pnl", Float),
    Column("costs", Float),
    Column("net_pnl", Float),
    Column("slippage", Float),
    Column("latency_ms", Float),
    Column("buy_leg", JSON),
    Column("sell_leg", JSON),
    Column("note", String(255)),
)


orders = Table(
    "paper_orders", metadata,
    Column("order_id", String(24), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), index=True),
    Column("broker", String(16), index=True),
    Column("exchange", String(8)),
    Column("symbol", String(32), index=True),
    Column("side", String(4)),
    Column("order_type", String(8)),
    Column("quantity", Integer),
    Column("price", Float),
    Column("status", String(12), index=True),
    Column("fill_price", Float),
    Column("latency_ms", Float),
    Column("reason", String(255)),
    Column("trade_id", String(16), index=True),
)


def normalise_url(url: str) -> str:
    # Render/Neon/Heroku give postgres:// or postgresql:// - use the psycopg 3 driver
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class TradeStore:
    def __init__(self, url: str):
        self.engine = create_engine(normalise_url(url), pool_pre_ping=True, future=True)
        metadata.create_all(self.engine)

    def save(self, trade: dict) -> None:
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(trades).values(
                    id=trade["id"], symbol=trade["symbol"], status=trade["status"],
                    quantity=trade["quantity"], expected_net=trade["expected_net"],
                    gross_pnl=trade["gross_pnl"], costs=trade["costs"], net_pnl=trade["net_pnl"],
                    slippage=trade["slippage"], latency_ms=trade["latency_ms"],
                    buy_leg=trade["buy"], sell_leg=trade["sell"], note=trade["note"][:255],
                ))
        except Exception as exc:
            log.warning("could not save trade %s: %s", trade.get("id"), exc)

    def save_order(self, order: dict) -> None:
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(orders).values(
                    order_id=order["order_id"], broker=order["broker"], exchange=order["exchange"],
                    symbol=order["symbol"], side=order["side"], order_type=order["order_type"],
                    quantity=order["quantity"], price=order["price"], status=order["status"],
                    fill_price=order["fill_price"], latency_ms=order["latency_ms"],
                    reason=order["reason"][:255], trade_id=order["tag"],
                ))
        except Exception as exc:
            log.warning("could not save order %s: %s", order.get("order_id"), exc)

    def order_history(self, limit: int = 200, broker: str | None = None, status: str | None = None) -> list[dict]:
        q = select(orders).order_by(orders.c.created_at.desc()).limit(limit)
        if broker:
            q = q.where(orders.c.broker == broker)
        if status:
            q = q.where(orders.c.status == status)
        with self.engine.connect() as conn:
            rows = conn.execute(q).mappings().all()
        return [dict(r) | {"created_at": r["created_at"].isoformat() if r["created_at"] else None} for r in rows]

    def history(self, limit: int = 200, symbol: str | None = None) -> list[dict]:
        q = select(trades).order_by(trades.c.created_at.desc()).limit(limit)
        if symbol:
            q = q.where(trades.c.symbol == symbol)
        with self.engine.connect() as conn:
            rows = conn.execute(q).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
            for k in ("buy_leg", "sell_leg"):
                if isinstance(d[k], str):
                    d[k] = json.loads(d[k])
            out.append(d)
        return out
