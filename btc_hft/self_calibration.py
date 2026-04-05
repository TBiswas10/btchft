"""
Self-calibrating parameter optimizer.

Every SELF_CAL_EVERY_N_FILLS fills, evaluates recent Sharpe,
fill rate, and regime PnL. Nudges key parameters toward
higher-performing regions using a simple gradient-free step.

Parameters tuned:
  - as_gamma (risk aversion): lower = tighter spreads, higher = wider
  - ofi_skew_bps: how aggressively to skew for OFI signal
  - min_net_edge_bps: threshold for accepting a trade

The calibrator never makes large jumps. Each adjustment is at most
SELF_CAL_STEP_SIZE * current_value, applied once per calibration event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .decision_policy import ExpectancyDecisionPolicy, calibrate_policy_from_outcomes

logger = logging.getLogger(__name__)


@dataclass
class CalibrationState:
    as_gamma: float
    ofi_skew_bps: float
    min_net_edge_bps: float
    calibration_count: int
    last_sharpe: float
    last_fill_rate: float
    adjustment_log: list[dict]


class SelfCalibrator:
    def __init__(
        self,
        initial_gamma: float = 0.1,
        initial_ofi_skew_bps: float = 1.5,
        initial_min_edge_bps: float = 1.5,
        step_size: float = 0.05,
        max_gamma: float = 0.5,
        min_gamma: float = 0.02,
        max_ofi_skew: float = 5.0,
        min_ofi_skew: float = 0.3,
        max_edge_bps: float = 25.0,
        min_edge_bps: float = 0.5,
        min_fills_for_policy_update: int = 25,
    ) -> None:
        self.as_gamma = initial_gamma
        self.ofi_skew_bps = initial_ofi_skew_bps
        self.min_net_edge_bps = initial_min_edge_bps
        self.step_size = step_size
        self.max_gamma = max_gamma
        self.min_gamma = min_gamma
        self.max_ofi_skew = max_ofi_skew
        self.min_ofi_skew = min_ofi_skew
        self.max_edge_bps = max_edge_bps
        self.min_edge_bps = min_edge_bps
        self.min_fills_for_policy_update = max(5, min_fills_for_policy_update)

        self._calibration_count = 0
        self._prev_sharpe: Optional[float] = None
        self._prev_fill_rate: Optional[float] = None
        self._last_gamma_direction: float = 0.0
        self._adjustment_log: list[dict] = []

    def maybe_calibrate(self, fill_count: int, every_n: int, analytics: dict) -> bool:
        if fill_count < every_n:
            return False
        if fill_count % every_n != 0:
            return False

        self._run_calibration(analytics)
        return True

    def _run_calibration(
        self,
        analytics: dict,
        policy: ExpectancyDecisionPolicy | None = None,
        outcomes: list | None = None,
        artifact_dir: Path | None = None,
    ) -> None:
        current_sharpe = float(analytics.get("sharpe", 0.0))
        current_fill_rate = float(analytics.get("fill_rate", 0.5))
        rolling_expectancy = float(analytics.get("rolling_post_cost_expectancy_bps", 0.0))
        ci_low = float(analytics.get("expectancy_ci_low_bps", 0.0))
        regime_expectancy = analytics.get("regime_expectancy_bps", {}) or {}

        self._calibration_count += 1
        old_gamma = self.as_gamma
        old_ofi = self.ofi_skew_bps
        old_edge = self.min_net_edge_bps
        direction_note = "init"

        if self._prev_sharpe is None:
            self._prev_sharpe = current_sharpe
            self._prev_fill_rate = current_fill_rate
            self._last_gamma_direction = 0.0
            self._log_adjustment(old_gamma, old_ofi, old_edge, direction_note, current_sharpe)
            return

        sharpe_improved = current_sharpe > self._prev_sharpe
        expectancy_positive = rolling_expectancy > 0 and ci_low > 0
        fill_rate_low = current_fill_rate < 0.10
        fill_rate_high = current_fill_rate > 0.80

        high_vol_expectancy = float(regime_expectancy.get("high_vol", 0.0))
        trend_expectancy = float(regime_expectancy.get("trend", 0.0))

        if expectancy_positive and sharpe_improved:
            if fill_rate_low and ci_low > 0:
                self._nudge_gamma(-1)
                self._nudge_edge(-1)
                direction_note = "expectancy_up_fill_low_relax"
            else:
                self._nudge_gamma(self._last_gamma_direction if self._last_gamma_direction != 0 else -1)
                direction_note = "expectancy_up_reinforce"
        else:
            reverse = -self._last_gamma_direction if self._last_gamma_direction != 0 else 1
            self._nudge_gamma(reverse)
            if fill_rate_high or rolling_expectancy < 0:
                self._nudge_edge(+1)
                direction_note = "expectancy_down_tighten"
            elif fill_rate_low:
                self._nudge_edge(-1)
                direction_note = "expectancy_down_fill_low_probe"
            else:
                direction_note = "expectancy_down_reverse_gamma"

        if trend_expectancy > 0 and high_vol_expectancy >= 0:
            self.ofi_skew_bps = min(self.max_ofi_skew, self.ofi_skew_bps * (1 + self.step_size))
        elif trend_expectancy < 0 or high_vol_expectancy < 0:
            self.ofi_skew_bps = max(self.min_ofi_skew, self.ofi_skew_bps * (1 - self.step_size))

        if policy is not None and outcomes and len(outcomes) >= self.min_fills_for_policy_update:
            artifact = calibrate_policy_from_outcomes(outcomes, output_dir=artifact_dir)
            policy.apply_artifact(artifact)
        elif policy is not None and outcomes is not None and len(outcomes) < self.min_fills_for_policy_update:
            logger.info(
                "Policy calibration skipped due to sparse outcomes",
                extra={
                    "event": "policy_calibration_skipped",
                    "outcome_count": len(outcomes),
                    "required_count": self.min_fills_for_policy_update,
                },
            )

        self._prev_sharpe = current_sharpe
        self._prev_fill_rate = current_fill_rate
        self._log_adjustment(old_gamma, old_ofi, old_edge, direction_note, current_sharpe)

        logger.info(
            "Self-calibration applied",
            extra={
                "event": "self_calibration",
                "calibration_count": self._calibration_count,
                "direction": direction_note,
                "old_gamma": old_gamma,
                "new_gamma": self.as_gamma,
                "old_ofi_skew": old_ofi,
                "new_ofi_skew": self.ofi_skew_bps,
                "old_edge_bps": old_edge,
                "new_edge_bps": self.min_net_edge_bps,
                "sharpe": current_sharpe,
                "fill_rate": current_fill_rate,
            },
        )

    def _nudge_gamma(self, direction: float) -> None:
        if direction == 0:
            return
        factor = 1 + self.step_size if direction > 0 else 1 - self.step_size
        self.as_gamma = max(self.min_gamma, min(self.max_gamma, self.as_gamma * factor))
        self._last_gamma_direction = direction

    def _nudge_edge(self, direction: float) -> None:
        if direction == 0:
            return
        factor = 1 + self.step_size if direction > 0 else 1 - self.step_size
        self.min_net_edge_bps = max(self.min_edge_bps, min(self.max_edge_bps, self.min_net_edge_bps * factor))

    def _log_adjustment(self, old_g, old_ofi, old_edge, note, sharpe):
        self._adjustment_log.append({
            "count": self._calibration_count,
            "note": note,
            "gamma": round(self.as_gamma, 5),
            "ofi_skew": round(self.ofi_skew_bps, 4),
            "edge_bps": round(self.min_net_edge_bps, 4),
            "sharpe": round(sharpe, 4),
        })
        if len(self._adjustment_log) > 100:
            self._adjustment_log = self._adjustment_log[-100:]

    @property
    def state(self) -> CalibrationState:
        return CalibrationState(
            as_gamma=self.as_gamma,
            ofi_skew_bps=self.ofi_skew_bps,
            min_net_edge_bps=self.min_net_edge_bps,
            calibration_count=self._calibration_count,
            last_sharpe=self._prev_sharpe or 0.0,
            last_fill_rate=self._prev_fill_rate or 0.0,
            adjustment_log=self._adjustment_log[-10:],
        )
