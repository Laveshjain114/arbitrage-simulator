"""FastAPI entry point: wires simulated feeds -> market data -> detector -> paper engine
(simulated broker accounts and orders), exposes a REST API and pushes state to the
dashboard over a WebSocket. Fully simulated: no real broker, exchange or money."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import INSTRUMENTS, RiskLimits, settings
from .db import TradeStore
from .engine.detector import ArbitrageDetector
from .engine.market import MarketDataEngine
from .engine.paper import PaperTradingEngine
from .feeds.base import Feed
from .feeds.simulator import MarketSimulator, SimulatedBrokerFeed
from .models import Broker, Quote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


class Platform:
    """Owns every component and their background tasks."""

    def __init__(self):
        self.symbols = list(INSTRUMENTS)
        self.risk = RiskLimits()
        self.market = MarketDataEngine()
        self.detector = ArbitrageDetector(self.market, self.risk)
        self.store = TradeStore(settings.database_url)
        self.paper = PaperTradingEngine(self.market, self.risk, settings.starting_capital,
                                        on_trade=self._persist, on_order=self._persist_order)
        self.engine_running = True
        self.started_at = time.time()
        self.feeds: list[Feed] = []
        self.tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self.market.listeners.append(self._on_quote)

    def _on_quote(self, q: Quote) -> None:
        if not self.engine_running:
            return
        opp = self.detector.evaluate(q.symbol)
        if opp is not None and opp.profitable:
            self.paper.submit(opp)

    def _persist(self, trade) -> None:
        asyncio.get_running_loop().run_in_executor(None, self.store.save, trade.to_dict())

    def _persist_order(self, order) -> None:
        with contextlib.suppress(RuntimeError):   # no running loop (e.g. during shutdown)
            asyncio.get_running_loop().run_in_executor(None, self.store.save_order, order.to_dict())

    async def start(self) -> None:
        sim = MarketSimulator(INSTRUMENTS, settings.tick_interval_ms)
        self.feeds = [SimulatedBrokerFeed(Broker.ANGEL, sim, latency_ms=(25, 110)),
                      SimulatedBrokerFeed(Broker.ZERODHA, sim, latency_ms=(15, 80))]
        self.tasks.append(asyncio.create_task(sim.run(self._stop)))
        for f in self.feeds:
            self.tasks.append(asyncio.create_task(f.run_forever(self.market.on_quote)))
        log.info("Started in full simulation mode (simulated feeds, accounts and orders)")

    async def stop(self) -> None:
        self._stop.set()
        for f in self.feeds:
            f.stop()
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    def status(self) -> dict:
        feeds = [f.status() for f in self.feeds]
        streaming = any(f["state"] == "connected" for f in feeds)
        return {
            "mode": "SIMULATION",
            "feeds": feeds,
            "market_data": "STREAMING" if streaming else "STALE",
            "engine": "RUNNING" if self.engine_running else "STOPPED",
            "paper_trading": "HALTED" if self.paper.halted_reason else ("ENABLED" if self.paper.enabled else "DISABLED"),
            "halted_reason": self.paper.halted_reason,
            "uptime_s": round(time.time() - self.started_at),
            "quote_updates": self.market.updates,
        }

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "ts": time.time() * 1000,
            "status": self.status(),
            "quotes": self.market.snapshot(self.symbols),
            "opportunities": self.detector.snapshot(),
            "detector": self.detector.stats(),
            "trades": [t.to_dict() for t in list(self.paper.trades)[:30]],
            "portfolio": self.paper.portfolio(),
            "orders": self.paper.orders(40),
            "order_stats": self.paper.order_stats(),
            "analytics": self.paper.analytics(),
            "risk": asdict(self.risk),
        }


platform: Platform | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global platform
    platform = Platform()
    await platform.start()
    yield
    await platform.stop()


app = FastAPI(title="Multi-Broker Arbitrage Simulator", version="1.0.0", lifespan=lifespan,
              description="Fully simulated NSE/BSE arbitrage: simulated market feeds, broker accounts and orders. "
                          "No real broker, exchange or money is involved.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------- REST API ----------------
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def get_status():
    return platform.status()


@app.get("/api/quotes")
def get_quotes():
    return platform.market.snapshot(platform.symbols)


@app.get("/api/opportunities")
def get_opportunities():
    return {"stats": platform.detector.stats(), "recent": platform.detector.snapshot()}


@app.get("/api/portfolio")
def get_portfolio():
    return platform.paper.portfolio()


@app.get("/api/analytics")
def get_analytics():
    return platform.paper.analytics()


@app.get("/api/trades")
def get_trades(limit: int = 100, symbol: str | None = None):
    return platform.store.history(min(limit, 1000), symbol)


@app.get("/api/accounts")
def get_accounts():
    return platform.paper.accounts()


@app.get("/api/orders")
def get_orders(limit: int = 100, broker: str | None = None, status: str | None = None, symbol: str | None = None):
    """Simulated order book (in memory, includes the current session's orders)."""
    return platform.paper.orders(min(limit, 1000), broker, status, symbol)


@app.get("/api/orders/history")
def get_order_history(limit: int = 100, broker: str | None = None, status: str | None = None):
    """Persisted simulated orders from the database."""
    return platform.store.order_history(min(limit, 1000), broker, status)


class RiskUpdate(BaseModel):
    min_net_pnl: float | None = Field(None, ge=0)
    max_quantity: int | None = Field(None, ge=1, le=100_000)
    max_notional: float | None = Field(None, gt=0)
    max_trades_per_minute: int | None = Field(None, ge=1, le=600)
    daily_loss_limit: float | None = Field(None, gt=0)
    max_quote_age_ms: float | None = Field(None, ge=50, le=10_000)
    symbol_cooldown_ms: float | None = Field(None, ge=0, le=60_000)


@app.get("/api/risk")
def get_risk():
    return asdict(platform.risk)


@app.put("/api/risk")
def update_risk(update: RiskUpdate):
    for k, v in update.model_dump(exclude_none=True).items():
        setattr(platform.risk, k, v)
    return asdict(platform.risk)


@app.post("/api/engine/{action}")
def engine_action(action: str):
    if action == "start":
        platform.engine_running = True
    elif action == "stop":
        platform.engine_running = False
    else:
        raise HTTPException(400, "action must be start or stop")
    return platform.status()


@app.post("/api/trading/{action}")
def trading_action(action: str):
    if action == "enable":
        platform.paper.enabled = True
        platform.paper.halted_reason = None
    elif action == "disable":
        platform.paper.enabled = False
    elif action == "reset":
        platform.paper.reset()
    else:
        raise HTTPException(400, "action must be enable, disable or reset")
    return platform.status()


# ---------------- WebSocket ----------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(platform.snapshot())
            await asyncio.sleep(settings.broadcast_interval_ms / 1000)
    except (WebSocketDisconnect, RuntimeError):
        pass


# ---------------- Frontend (built React app) ----------------
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
