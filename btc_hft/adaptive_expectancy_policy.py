from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import json
from statistics import mean, pstdev

from .decision_policy import (
    CalibrationArtifact,
    DecisionInput,
    DecisionOutcome,
    ExpectancyDecisionPolicy,
    TradeDecision,
    _notional_bucket,
    calibrate_policy_from_outcomes,
    load_latest_calibration_artifact,
)


@dataclass(frozen=True)
class EmpiricalCostEstimate:
    fee_bps: float
    slippage_bps: float
    adverse_selection_bps: float
    fill_prob: float


class AdaptiveExpectancyPolicy(ExpectancyDecisionPolicy):
    """Adaptive post-cost expectancy policy with rolling empirical costs.

    This policy keeps the same evaluate() interface as ExpectancyDecisionPolicy,
    but derives per-regime/per-notional empirical costs from observed outcomes,
    applies low-signal suppression, and can enforce artifact-readiness gates.
    """

    def __init__(
        self,
        *args,
        calibration_dir: Path | None = None,
        enforce_artifact_readiness: bool = False,
        min_artifacts_per_regime: int = 2,
        rolling_window: int = 300,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._rolling_window = max(50, int(rolling_window))
        self._enforce_artifact_readiness = bool(enforce_artifact_readiness)
        self._min_artifacts_per_regime = max(1, int(min_artifacts_per_regime))
        self._artifact_counts_by_regime: dict[str, int] = {
            "quiet": 0,
            "normal": 0,
            "trend": 0,
            "high_vol": 0,
            "warmup": 0,
            "unknown": 0,
        }

        self._realized_by_regime: dict[str, deque[float]] = {
            k: deque(maxlen=self._rolling_window) for k in self._artifact_counts_by_regime
        }
        self._predicted_by_regime: dict[str, deque[float]] = {
            k: deque(maxlen=self._rolling_window) for k in self._artifact_counts_by_regime
        }
        self._slippage_by_key: dict[str, deque[float]] = {}
        self._adverse_by_key: dict[str, deque[float]] = {}
        self._fill_prob_by_key: dict[str, deque[float]] = {}

        if calibration_dir is not None:
            self.refresh_artifact_readiness(calibration_dir)

    def refresh_artifact_readiness(self, calibration_dir: Path) -> None:
        counts = {k: 0 for k in self._artifact_counts_by_regime}
        if calibration_dir.exists():
            for path in sorted(calibration_dir.glob("expectancy_*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for regime, params in (payload.get("regime_params") or {}).items():
                    if regime in counts and isinstance(params, dict) and int(params.get("sample_count", 0)) > 0:
                        counts[regime] += 1
        self._artifact_counts_by_regime = counts

    def artifact_ready(self, regime: str) -> bool:
        if not self._enforce_artifact_readiness:
            return True
        return self._artifact_counts_by_regime.get(regime, 0) >= self._min_artifacts_per_regime

    def signal_confidence(self, inp: DecisionInput) -> float:
        ofi_strength = min(abs(inp.ofi_score), 1.0)
        momentum_strength = min(abs(inp.momentum_bps) / 10.0, 1.0)
        queue_strength = 0.6 if inp.queue_position == "front" else 0.35 if inp.queue_position == "unknown" else 0.15
        regime_bias = 0.6 if inp.regime in {"normal", "trend", "high_vol"} else 0.35
        conf = 0.45 * ofi_strength + 0.35 * momentum_strength + 0.20 * queue_strength
        conf *= regime_bias
        return min(max(conf, 0.0), 1.0)

    def estimate_empirical_costs(
        self,
        regime: str,
        quote_notional_usd: float,
        queue_position: str,
        maker_fee_bps: float,
        fallback_slippage_bps: float,
        fallback_adverse_bps: float,
        observed_fill_rate: float | None = None,
    ) -> EmpiricalCostEstimate:
        bucket = _notional_bucket(quote_notional_usd)
        key = f"{regime}|{bucket}"

        def _avg_or(default: float, series: dict[str, deque[float]]) -> float:
            vals = list(series.get(key, []))
            if vals:
                return float(mean(vals))
            regime_vals: list[float] = []
            for k, rows in series.items():
                if k.startswith(f"{regime}|") and rows:
                    regime_vals.extend(rows)
            return float(mean(regime_vals)) if regime_vals else default

        slippage = max(0.0, _avg_or(fallback_slippage_bps, self._slippage_by_key))
        adverse = max(0.0, _avg_or(fallback_adverse_bps, self._adverse_by_key))
        fill_prob_default = self.estimate_fill_probability(regime, queue_position, observed_fill_rate)
        fill_prob = min(max(_avg_or(fill_prob_default, self._fill_prob_by_key), 0.01), 0.99)

        return EmpiricalCostEstimate(
            fee_bps=max(0.0, maker_fee_bps),
            slippage_bps=slippage,
            adverse_selection_bps=adverse,
            fill_prob=fill_prob,
        )

    def observe_outcome(self, outcome: DecisionOutcome) -> None:
        regime = outcome.regime if outcome.regime in self._realized_by_regime else "unknown"
        self._realized_by_regime[regime].append(float(outcome.realized_net_bps))
        self._predicted_by_regime[regime].append(float(outcome.expected_net_bps))

        key = f"{regime}|{_notional_bucket(outcome.quote_notional_usd)}"
        self._slippage_by_key.setdefault(key, deque(maxlen=self._rolling_window)).append(float(outcome.slippage_bps))
        self._adverse_by_key.setdefault(key, deque(maxlen=self._rolling_window)).append(float(outcome.adverse_selection_bps))
        self._fill_prob_by_key.setdefault(key, deque(maxlen=self._rolling_window)).append(float(outcome.fill_prob))

    def rolling_regime_correlation(self, regime: str) -> float:
        pred = list(self._predicted_by_regime.get(regime, []))
        real = list(self._realized_by_regime.get(regime, []))
        if len(pred) != len(real) or len(pred) < 2:
            return 0.0
        mx = mean(pred)
        my = mean(real)
        num = sum((a - mx) * (b - my) for a, b in zip(pred, real))
        denx = sum((a - mx) ** 2 for a in pred) ** 0.5
        deny = sum((b - my) ** 2 for b in real) ** 0.5
        den = denx * deny
        if den <= 1e-12:
            return 0.0
        return float(num / den)

    def should_suspend_regime(self, regime: str, min_points: int = 20) -> bool:
        vals = list(self._realized_by_regime.get(regime, []))
        if len(vals) < min_points:
            return False
        mu = mean(vals)
        sigma = pstdev(vals) if len(vals) > 1 else 0.0
        return mu < 0.0 and (mu + 0.35 * sigma) < 0.0

    def evaluate(self, inp: DecisionInput) -> TradeDecision:
        if not self.artifact_ready(inp.regime):
            return TradeDecision(
                should_trade=False,
                expected_net_bps=-1e-9,
                confidence=0.0,
                threshold_used=self._threshold_for(inp.regime, 0.0, inp.quote_notional_usd),
                reason="insufficient_regime_artifacts",
                size_multiplier=0.0,
                spread_multiplier=1.0,
            )

        signal_conf = self.signal_confidence(inp)
        if signal_conf < 0.10:
            threshold = self._threshold_for(inp.regime, signal_conf, inp.quote_notional_usd)
            return TradeDecision(
                should_trade=False,
                expected_net_bps=-1e-9,
                confidence=signal_conf,
                threshold_used=threshold,
                reason="low_information_signal_suppressed",
                size_multiplier=0.0,
                spread_multiplier=1.0,
            )

        if self.should_suspend_regime(inp.regime):
            threshold = self._threshold_for(inp.regime, signal_conf, inp.quote_notional_usd)
            return TradeDecision(
                should_trade=False,
                expected_net_bps=-1e-9,
                confidence=signal_conf,
                threshold_used=threshold,
                reason="negative_regime_expectancy_suspend",
                size_multiplier=0.0,
                spread_multiplier=1.0,
            )

        decision = super().evaluate(inp)

        if inp.regime == "normal" and decision.should_trade:
            edge_excess = decision.expected_net_bps - decision.threshold_used
            if inp.estimated_fill_prob < 0.35 and edge_excess < 0.50:
                return TradeDecision(
                    should_trade=False,
                    expected_net_bps=decision.expected_net_bps,
                    confidence=decision.confidence,
                    threshold_used=decision.threshold_used,
                    reason="normal_low_fill_soft_block",
                    size_multiplier=0.0,
                    spread_multiplier=1.0,
                )

            target_fill = 0.65 if inp.queue_position == "front" else 0.55 if inp.queue_position == "unknown" else 0.45
            fill_gap = max(0.0, target_fill - inp.estimated_fill_prob)
            if fill_gap > 0.0:
                # Tighten normal-regime quotes for better queue capture when the edge is already positive.
                spread_multiplier = max(0.72, 1.0 - min(0.28, fill_gap * 0.60))
                size_multiplier = max(0.75, 1.0 - min(0.25, fill_gap * 0.50))
                return TradeDecision(
                    should_trade=True,
                    expected_net_bps=decision.expected_net_bps,
                    confidence=decision.confidence,
                    threshold_used=decision.threshold_used,
                    reason="post_cost_expectancy_ok_normal_fill_assist",
                    size_multiplier=size_multiplier,
                    spread_multiplier=spread_multiplier,
                )

        if not decision.should_trade and signal_conf >= 0.20 and decision.size_multiplier <= 0.0:
            return TradeDecision(
                should_trade=False,
                expected_net_bps=decision.expected_net_bps,
                confidence=decision.confidence,
                threshold_used=decision.threshold_used,
                reason=decision.reason,
                size_multiplier=0.25,
                spread_multiplier=max(decision.spread_multiplier, 1.15),
            )
        return decision


__all__ = [
    "AdaptiveExpectancyPolicy",
    "CalibrationArtifact",
    "DecisionInput",
    "DecisionOutcome",
    "EmpiricalCostEstimate",
    "ExpectancyDecisionPolicy",
    "TradeDecision",
    "calibrate_policy_from_outcomes",
    "load_latest_calibration_artifact",
]
