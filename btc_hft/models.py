from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Signal(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class QuoteSnapshot:
    bid: float = 0.0
    ask: float = 0.0
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def mid(self) -> float:
        if self.bid <= 0 or self.ask <= 0:
            return 0.0
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        mid = self.mid
        if mid <= 0:
            return 0.0
        return ((self.ask - self.bid) / mid) * 10000


@dataclass
class PositionState:
    qty_btc: float = 0.0
    avg_entry_price: float = 0.0
    entry_time: Optional[datetime] = None

    @property
    def side(self) -> str:
        if self.qty_btc > 0:
            return "long"
        if self.qty_btc < 0:
            return "short"
        return "flat"


@dataclass
class RuntimeState:
    last_quote: QuoteSnapshot = field(default_factory=QuoteSnapshot)
    position: PositionState = field(default_factory=PositionState)

    realized_pnl_usd: float = 0.0
    estimated_fees_usd: float = 0.0
    estimated_slippage_usd: float = 0.0
    funding_pnl_usd: float = 0.0
    consecutive_losses: int = 0
    last_trade_time: Optional[datetime] = None
    blocked_reason: Optional[str] = None
    last_funding_applied_at: Optional[datetime] = None
    trading_day_utc: Optional[str] = None
    session_started_at: Optional[datetime] = None

    trade_count: int = 0
    wins: int = 0
    losses: int = 0

    daily_realized_pnl_usd: float = 0.0
    daily_estimated_fees_usd: float = 0.0
    daily_estimated_slippage_usd: float = 0.0
    daily_funding_pnl_usd: float = 0.0
    daily_trade_count: int = 0
