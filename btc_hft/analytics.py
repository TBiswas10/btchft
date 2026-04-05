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

from .decision_policy import DecisionOutcome


@dataclass
class FillRecord:
    realized_pnl: float
    spread_capture: float
    ofi_score: float
    regime: str
    vol_bps: float
    queue_position: str
    side: str
    expected_net_bps: float = 0.0
    realized_net_bps: float = 0.0
    confidence: float = 0.0
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    adverse_selection_bps: float = 0.0
    expected_fill_prob: float = 0.0


class PerformanceAnalytics:
    """Rolling performance analytics over a configurable window."""

    REGIMES = ("quiet", "normal", "trend", "high_vol", "warmup", "unknown")

    def __init__(self, window: int = 300) -> None:
        self.window = max(50, window)
        self._fills: deque[FillRecord] = deque(maxlen=self.window)
        self._edge_blocks: int = 0
        self._toxicity_vetoes: int = 0
        self._total_fills: int = 0
        self._decisions: deque[dict] = deque(maxlen=self.window * 2)

    def record_fill(
        self,
        realized_pnl: float,
        spread_capture: float,
        ofi_score: float,
        regime: str,
        vol_bps: float,
        queue_position: str,
        side: str,
        expected_net_bps: float = 0.0,
        realized_net_bps: float = 0.0,
        confidence: float = 0.0,
        fee_bps: float = 0.0,
        slippage_bps: float = 0.0,
        adverse_selection_bps: float = 0.0,
        expected_fill_prob: float = 0.0,
    ) -> None:
        self._fills.append(FillRecord(
            realized_pnl=realized_pnl,
            spread_capture=spread_capture,
            ofi_score=ofi_score,
            regime=regime,
            vol_bps=vol_bps,
            queue_position=queue_position,
            side=side,
            expected_net_bps=expected_net_bps,
            realized_net_bps=realized_net_bps,
            confidence=confidence,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            adverse_selection_bps=adverse_selection_bps,
            expected_fill_prob=expected_fill_prob,
        ))
        self._total_fills += 1

    def record_decision(self, regime: str, should_trade: bool, expected_net_bps: float, threshold_bps: float, confidence: float, reason: str) -> None:
        self._decisions.append(
            {
                "regime": regime,
                "should_trade": bool(should_trade),
                "expected_net_bps": float(expected_net_bps),
                "threshold_bps": float(threshold_bps),
                "confidence": float(confidence),
                "reason": reason,
            }
        )

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

    def rolling_post_cost_expectancy_bps(self) -> float:
        if not self._fills:
            return 0.0
        values = [f.realized_net_bps for f in self._fills]
        return sum(values) / len(values)

    def confidence_interval_low_bps(self) -> float:
        if len(self._fills) < 10:
            return 0.0
        values = [f.realized_net_bps for f in self._fills]
        mean_val = statistics.mean(values)
        try:
            std_val = statistics.stdev(values)
        except statistics.StatisticsError:
            return 0.0
        margin = 1.96 * std_val / math.sqrt(max(len(values), 1))
        return mean_val - margin

    def regime_expectancy_bps(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for regime in self.REGIMES:
            rows = [f.realized_net_bps for f in self._fills if f.regime == regime]
            if rows:
                out[regime] = round(sum(rows) / len(rows), 6)
        return out

    def decision_outcomes(self) -> list[DecisionOutcome]:
        outcomes: list[DecisionOutcome] = []
        for fill in self._fills:
            outcomes.append(
                DecisionOutcome(
                    regime=fill.regime,
                    queue_position=fill.queue_position,
                    expected_net_bps=fill.expected_net_bps,
                    realized_net_bps=fill.realized_net_bps,
                    expected_capture_bps=fill.spread_capture,
                    fill_prob=fill.expected_fill_prob,
                    confidence=fill.confidence,
                    fee_bps=fill.fee_bps,
                    slippage_bps=fill.slippage_bps,
                    adverse_selection_bps=fill.adverse_selection_bps,
                )
            )
        return outcomes

    def snapshot(self) -> dict:
        confidence_avg = (
            sum(item["confidence"] for item in self._decisions) / len(self._decisions)
            if self._decisions
            else 0.0
        )
        return {
            "sharpe": round(self.sharpe, 4),
            "win_rate": round(self.win_rate, 4),
            "avg_spread_capture_bps": round(self.avg_spread_capture_bps, 4),
            "fill_rate": round(self.fill_rate, 4),
            "rolling_post_cost_expectancy_bps": round(self.rolling_post_cost_expectancy_bps(), 6),
            "expectancy_ci_low_bps": round(self.confidence_interval_low_bps(), 6),
            "regime_expectancy_bps": self.regime_expectancy_bps(),
            "decision_confidence_avg": round(confidence_avg, 4),
            "total_fills": self._total_fills,
            "edge_blocks": self._edge_blocks,
            "toxicity_vetoes": self._toxicity_vetoes,
            "regime_pnl": self.regime_pnl_summary(),
            "queue_pnl": self.queue_pnl_summary(),
        }
