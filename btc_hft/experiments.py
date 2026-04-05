from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ParamSet:
    name: str
    target_spread_bps: float
    inventory_skew_bps: float
    reprice_bps: float


@dataclass(frozen=True)
class WeeklyMetric:
    realized_pnl_usd: float
    max_drawdown_usd: float
    win_rate_pct: float
    slippage_usd: float
    trade_count: int = 0


@dataclass(frozen=True)
class SweepResult:
    generated_at: str
    champion_name: str
    challenger_name: str
    winner_name: str
    champion_score: float
    challenger_score: float
    rationale: str


class ChampionChallengerWeeklySweep:
    """Weekly single-pair selection, intentionally simple for solo operations."""

    def _score(self, metric: WeeklyMetric) -> float:
        # Penalize drawdown/slippage while rewarding PnL and win rate.
        return (
            metric.realized_pnl_usd
            + (metric.win_rate_pct * 2.0)
            - (metric.max_drawdown_usd * 0.7)
            - (metric.slippage_usd * 1.5)
        )

    @staticmethod
    def _promotion_gate(champion: WeeklyMetric, challenger: WeeklyMetric) -> tuple[bool, str]:
        if challenger.trade_count < 30:
            return False, "challenger_insufficient_sample"
        if challenger.realized_pnl_usd < (champion.realized_pnl_usd * 1.02):
            return False, "challenger_pnl_not_materially_better"
        if challenger.max_drawdown_usd > (champion.max_drawdown_usd * 1.05):
            return False, "challenger_drawdown_too_high"
        if challenger.win_rate_pct < (champion.win_rate_pct - 1.0):
            return False, "challenger_win_rate_too_low"
        if challenger.slippage_usd > (champion.slippage_usd * 1.10):
            return False, "challenger_slippage_too_high"
        return True, "challenger_passed_all_promotion_gates"

    def run(
        self,
        champion: ParamSet,
        challenger: ParamSet,
        champion_metric: WeeklyMetric,
        challenger_metric: WeeklyMetric,
    ) -> SweepResult:
        champion_score = self._score(champion_metric)
        challenger_score = self._score(challenger_metric)
        promotion_ok, promotion_reason = self._promotion_gate(champion_metric, challenger_metric)

        if promotion_ok and challenger_score > champion_score:
            winner = challenger.name
            rationale = "challenger_promoted_risk_adjusted"
        else:
            winner = champion.name
            rationale = f"champion_retained_{promotion_reason}"

        return SweepResult(
            generated_at=datetime.now(timezone.utc).isoformat(),
            champion_name=champion.name,
            challenger_name=challenger.name,
            winner_name=winner,
            champion_score=round(champion_score, 4),
            challenger_score=round(challenger_score, 4),
            rationale=rationale,
        )
