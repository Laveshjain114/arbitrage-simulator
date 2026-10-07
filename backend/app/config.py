"""Runtime settings, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

INSTRUMENTS: dict[str, float] = {
    # symbol -> reference price used to start the simulator
    "RELIANCE": 1420.0,
    "TCS": 3145.0,
    "INFY": 1530.0,
    "HDFCBANK": 1985.0,
    "ICICIBANK": 1410.0,
    "SBIN": 815.0,
    "ITC": 412.0,
    "LT": 3620.0,
}


@dataclass
class RiskLimits:
    """Paper-trading guard rails. Editable at runtime from the dashboard."""
    min_net_pnl: float = 1.0           # only trade if expected net P&L (₹) is at least this
    max_quantity: int = 500            # shares per trade
    max_notional: float = 500_000.0    # ₹ per leg
    max_trades_per_minute: int = 20
    daily_loss_limit: float = 2_000.0  # stop auto-trading after this much realised loss (₹)
    max_quote_age_ms: float = 1_000.0  # ignore quotes older than this
    symbol_cooldown_ms: float = 1_500.0


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./arbitrage.db"))
    starting_capital: float = float(os.environ.get("STARTING_CAPITAL", "1000000"))
    tick_interval_ms: int = int(os.environ.get("TICK_INTERVAL_MS", "250"))
    broadcast_interval_ms: int = int(os.environ.get("BROADCAST_INTERVAL_MS", "500"))


settings = Settings()
