import asyncio

import pytest

from app.config import RiskLimits
from app.engine.costs import arbitrage_costs, leg_brokerage
from app.engine.detector import ArbitrageDetector
from app.engine.market import MarketDataEngine
from app.engine.paper import PaperTradingEngine
from app.models import Broker, Exchange, Quote, now_ms


def q(broker, ex, bid, ask, qty=1000, symbol="RELIANCE"):
    t = now_ms()
    return Quote(broker, ex, symbol, bid, ask, qty, qty, exchange_ts=t, received_ts=t)


# ---------------- costs ----------------
def test_brokerage_is_capped_at_20():
    assert leg_brokerage(Broker.ZERODHA, 1_000_000) == 20.0
    assert leg_brokerage(Broker.ZERODHA, 10_000) == pytest.approx(3.0)   # 0.03%


def test_costs_components_are_positive_and_sum():
    c = arbitrage_costs(Broker.ANGEL, Exchange.NSE, 1420.0, Broker.ZERODHA, Exchange.BSE, 1421.0, 100)
    parts = [c.brokerage, c.stt, c.exchange_txn, c.sebi, c.stamp_duty, c.gst]
    assert all(p > 0 for p in parts)
    assert c.total == pytest.approx(sum(parts))
    assert c.stt == pytest.approx(1421.0 * 100 * 0.00025)   # STT on the sell side only


# ---------------- detector ----------------
def test_detects_buy_nse_sell_bse():
    m = MarketDataEngine()
    d = ArbitrageDetector(m, RiskLimits())
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.90, 1420.00))
    m.on_quote(q(Broker.ZERODHA, Exchange.BSE, 1423.00, 1423.20))
    opp = d.evaluate("RELIANCE")
    assert opp.buy_exchange is Exchange.NSE and opp.sell_exchange is Exchange.BSE
    assert opp.buy_price == 1420.00 and opp.sell_price == 1423.00
    assert opp.gross_spread == pytest.approx(3.0)
    assert opp.quantity == 352       # limited by max_notional 5,00,000 / 1420
    assert opp.profitable


def test_no_opportunity_when_books_overlap():
    m = MarketDataEngine()
    d = ArbitrageDetector(m, RiskLimits())
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.90, 1420.00))
    m.on_quote(q(Broker.ANGEL, Exchange.BSE, 1419.85, 1420.10))
    assert d.evaluate("RELIANCE") is None


def test_tiny_spread_is_detected_but_not_profitable():
    m = MarketDataEngine()
    d = ArbitrageDetector(m, RiskLimits())
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.90, 1420.00))
    m.on_quote(q(Broker.ANGEL, Exchange.BSE, 1420.05, 1420.20))
    opp = d.evaluate("RELIANCE")
    assert opp is not None and not opp.profitable    # costs eat the ₹0.05 spread


def test_stale_quotes_are_ignored():
    m = MarketDataEngine()
    d = ArbitrageDetector(m, RiskLimits(max_quote_age_ms=100))
    old = q(Broker.ANGEL, Exchange.BSE, 1430.0, 1430.2)
    old.received_ts -= 5_000
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.9, 1420.0))
    m.on_quote(old)
    assert d.evaluate("RELIANCE") is None


def test_episodes_counted_once_per_open_spread():
    m = MarketDataEngine()
    d = ArbitrageDetector(m, RiskLimits())
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.9, 1420.0))
    m.on_quote(q(Broker.ANGEL, Exchange.BSE, 1423.0, 1423.2))
    d.evaluate("RELIANCE"); d.evaluate("RELIANCE"); d.evaluate("RELIANCE")
    assert d.episodes == 1


# ---------------- paper engine ----------------
@pytest.mark.asyncio
async def test_paper_trade_completes_and_books_pnl():
    m = MarketDataEngine()
    risk = RiskLimits()
    d = ArbitrageDetector(m, risk)
    p = PaperTradingEngine(m, risk, 1_000_000, seed=1)
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.9, 1420.0))
    m.on_quote(q(Broker.ZERODHA, Exchange.BSE, 1423.0, 1423.2))
    opp = d.evaluate("RELIANCE")
    p.submit(opp)
    await asyncio.sleep(0.3)
    t = p.trades[0]
    assert t.status == "COMPLETED"
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.costs)
    assert p.realized_pnl == pytest.approx(t.net_pnl)


@pytest.mark.asyncio
async def test_legging_risk_when_sell_side_disappears():
    m = MarketDataEngine()
    risk = RiskLimits()
    d = ArbitrageDetector(m, risk)
    p = PaperTradingEngine(m, risk, 1_000_000, seed=2)
    import app.engine.paper as paper_mod
    paper_mod.UNWIND_DELAY_S = (0.05, 0.05)
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.9, 1420.0))
    m.on_quote(q(Broker.ZERODHA, Exchange.BSE, 1423.0, 1423.2))
    opp = d.evaluate("RELIANCE")
    p.submit(opp)
    m.on_quote(q(Broker.ZERODHA, Exchange.BSE, 1419.0, 1419.2))   # BSE bid collapses before the sell fills
    await asyncio.sleep(0.4)
    t = p.trades[0]
    assert t.status == "LEGGED"
    assert t.buy.status == "FILLED" and t.sell.status == "MISSED"
    assert not p.positions          # unwound
    exits = [o for o in p.orders() if o["order_type"] == "MARKET"]
    assert len(exits) == 1 and exits[0]["side"] == "SELL" and exits[0]["status"] == "COMPLETE"


@pytest.mark.asyncio
async def test_risk_limits_block_trades():
    m = MarketDataEngine()
    risk = RiskLimits(min_net_pnl=10_000)    # unrealistically high bar
    d = ArbitrageDetector(m, risk)
    p = PaperTradingEngine(m, risk, 1_000_000)
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.9, 1420.0))
    m.on_quote(q(Broker.ZERODHA, Exchange.BSE, 1423.0, 1423.2))
    p.submit(d.evaluate("RELIANCE"))
    await asyncio.sleep(0.3)
    assert len(p.trades) == 0


@pytest.mark.asyncio
async def test_daily_loss_limit_halts_trading():
    m = MarketDataEngine()
    p = PaperTradingEngine(m, RiskLimits(daily_loss_limit=100), 1_000_000)
    p.realized_pnl = -150
    p._check_daily_loss()
    assert p.halted_reason is not None


# ---------------- simulated broker ----------------
@pytest.mark.asyncio
async def test_completed_trade_creates_two_filled_orders_and_books_accounts():
    m = MarketDataEngine()
    risk = RiskLimits()
    d = ArbitrageDetector(m, risk)
    p = PaperTradingEngine(m, risk, 1_000_000, seed=1)
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.9, 1420.0))
    m.on_quote(q(Broker.ZERODHA, Exchange.BSE, 1423.0, 1423.2))
    p.submit(d.evaluate("RELIANCE"))
    await asyncio.sleep(0.3)
    t = p.trades[0]
    orders = p.orders()
    assert {o["order_id"] for o in orders} == {t.buy.order_id, t.sell.order_id}
    assert all(o["status"] == "COMPLETE" and o["tag"] == t.id for o in orders)
    balances = sum(a["balance"] for a in p.accounts())
    assert balances == pytest.approx(1_000_000 + t.net_pnl)
    assert all(a["used_margin"] == 0 for a in p.accounts())    # margin released after fills


@pytest.mark.asyncio
async def test_order_rejected_for_insufficient_margin():
    from app.broker.simulated import SimulatedBroker
    m = MarketDataEngine()
    m.on_quote(q(Broker.ANGEL, Exchange.NSE, 1419.9, 1420.0))
    b = SimulatedBroker(Broker.ANGEL, m, capital=10_000)
    o = await b.place_order("RELIANCE", Exchange.NSE, "BUY", 100, "LIMIT", 1420.0)   # needs ₹28,400 margin
    assert o.status == "REJECTED" and "margin" in o.reason


@pytest.mark.asyncio
async def test_limit_order_cancelled_when_price_moves_away():
    from app.broker.simulated import SimulatedBroker
    m = MarketDataEngine()
    m.on_quote(q(Broker.ZERODHA, Exchange.BSE, 1419.9, 1425.0))
    b = SimulatedBroker(Broker.ZERODHA, m, capital=1_000_000)
    o = await b.place_order("RELIANCE", Exchange.BSE, "BUY", 10, "LIMIT", 1420.0)
    assert o.status == "CANCELLED" and o.fill_price is None
    assert b.account.used_margin == 0
