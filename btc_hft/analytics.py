"""
Performance analytics engine.

Tracks rolling Sharpe ratio, win rate, fill quality, spread capture,
and per-regime PnL attribution. Used by self-calibration loop.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass


@dataclass
class FillRecord:
    realized_pnl: float
    spread_capture: float
    ofi_score: float
    regime: str
    vol_bps: float
    queue_position: str
    side: str


class PerformanceAnalytics:
    """Rolling performance analytics over a configurable window."""

    REGIMES = ("quiet", "normal", "trend", "high_vol", "warmup", "unknown")

    def __init__(self, window: int = 300) -> None:
        self.window = max(50, window)
        self._fills: deque[FillRecord] = deque(maxlen=self.window)
        self._edge_blocks: int = 0
        self._toxicity_vetoes: int = 0
        self._total_fills: int = 0

    def record_fill(self, realized_pnl: float, spread_capture: float, ofi_score: float, regime: str, vol_bps: float, queue_position: str, side: str) -> None:
        self._fills.append(FillRecord(
            realized_pnl=realized_pnl,
            spread_capture=spread_capture,
            ofi_score=ofi_score,
            regime=regime,
            vol_bps=vol_bps,
            queue_position=queue_position,
            side=side,
        ))
        self._total_fills += 1

    def record_edge_block(self) -> None:
        self._edge_blocks += 1

    def record_toxicity_veto(self) -> None:
        self._toxicity_vetoes += 1

    @property
    def sharpe(self) -> float:
        if len(self._fills) < 10:
            return 0.0
        returns = [f.realized_pnl for f in self._fills]
        mean = statistics.mean(returns)
        try:
            std = statistics.stdev(returns)
        except statistics.StatisticsError:
            return 0.0
        if std < 1e-12:
            return 0.0
        return (mean / std) * math.sqrt(100 * 252)

    @property
    def win_rate(self) -> float:
        if not self._fills:
            return 0.0
        wins = sum(1 for f in self._fills if f.realized_pnl > 0)
        return wins / len(self._fills)

    @property
    def avg_spread_capture_bps(self) -> float:
        if not self._fills:
            return 0.0
        return sum(f.spread_capture for f in self._fills) / len(self._fills)

    @property
    def fill_rate(self) -> float:
        total = self._total_fills + self._edge_blocks
        if total == 0:
            return 0.0
        return self._total_fills / total

    def regime_pnl_summary(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for regime in self.REGIMES:
            fills = [f for f in self._fills if f.regime == regime]
            if not fills:
                continue
            pnls = [f.realized_pnl for f in fills]
            result[regime] = {
                "count": len(fills),
                "total_usd": round(sum(pnls), 6),
                "avg_usd": round(sum(pnls) / len(pnls), 6),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
            }
        return result

    def queue_pnl_summary(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for pos in ("front", "back", "unknown"):
            fills = [f for f in self._fills if f.queue_position == pos]
            if not fills:
                continue
            pnls = [f.realized_pnl for f in fills]
            result[pos] = {
                "count": len(fills),
                "avg_usd": round(sum(pnls) / len(pnls), 6),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
            }
        return result

    def snapshot(self) -> dict:
        return {
            "sharpe": round(self.sharpe, 4),
            "win_rate": round(self.win_rate, 4),
            "avg_spread_capture_bps": round(self.avg_spread_capture_bps, 4),
            "fill_rate": round(self.fill_rate, 4),
            "total_fills": self._total_fills,
            "edge_blocks": self._edge_blocks,
            "toxicity_vetoes": self._toxicity_vetoes,
            "regime_pnl": self.regime_pnl_summary(),
            "queue_pnl": self.queue_pnl_summary(),
        }
