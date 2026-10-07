"""Approximate Indian equity intraday transaction costs.

Rates are indicative (based on published 2024-25 schedules) and should be checked
against each broker's current charge sheet before relying on them.
"""
from __future__ import annotations

from ..models import Broker, CostBreakdown, Exchange

# Brokerage per executed order: flat cap or percentage of turnover, whichever is lower
BROKERAGE = {
    Broker.ZERODHA: {"flat": 20.0, "pct": 0.0003},   # ₹20 or 0.03%
    Broker.ANGEL: {"flat": 20.0, "pct": 0.0003},     # ₹20 or 0.03% (intraday)
}
STT_SELL_INTRADAY = 0.00025          # 0.025% on the sell side
EXCHANGE_TXN = {Exchange.NSE: 0.0000297, Exchange.BSE: 0.0000375}  # % of turnover
SEBI_FEE = 10 / 1e7                  # ₹10 per crore
STAMP_DUTY_BUY_INTRADAY = 0.00003    # 0.003% on the buy side
GST = 0.18                           # on brokerage + exchange txn + SEBI fee


def leg_brokerage(broker: Broker, turnover: float) -> float:
    rule = BROKERAGE[broker]
    return min(rule["flat"], turnover * rule["pct"])


def arbitrage_costs(buy_broker: Broker, buy_exchange: Exchange, buy_price: float,
                    sell_broker: Broker, sell_exchange: Exchange, sell_price: float,
                    quantity: int) -> CostBreakdown:
    """Total cost of buying on one venue and selling on another (both legs)."""
    buy_turnover = buy_price * quantity
    sell_turnover = sell_price * quantity

    brokerage = leg_brokerage(buy_broker, buy_turnover) + leg_brokerage(sell_broker, sell_turnover)
    stt = sell_turnover * STT_SELL_INTRADAY
    exchange_txn = buy_turnover * EXCHANGE_TXN[buy_exchange] + sell_turnover * EXCHANGE_TXN[sell_exchange]
    sebi = (buy_turnover + sell_turnover) * SEBI_FEE
    stamp = buy_turnover * STAMP_DUTY_BUY_INTRADAY
    gst = (brokerage + exchange_txn + sebi) * GST
    return CostBreakdown(brokerage=brokerage, stt=stt, exchange_txn=exchange_txn,
                         sebi=sebi, stamp_duty=stamp, gst=gst)
