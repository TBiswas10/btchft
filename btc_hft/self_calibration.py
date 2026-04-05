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
from typing import Optional

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
        initial_min_edge_bps: float = 12.0,
        step_size: float = 0.05,
        max_gamma: float = 0.5,
        min_gamma: float = 0.02,
        max_ofi_skew: float = 5.0,
        min_ofi_skew: float = 0.3,
        max_edge_bps: float = 25.0,
        min_edge_bps: float = 5.0,
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

    def _run_calibration(self, analytics: dict) -> None:
        current_sharpe = analytics.get("sharpe", 0.0)
        current_fill_rate = analytics.get("fill_rate", 0.5)

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
        fill_rate_low = current_fill_rate < 0.30
        fill_rate_high = current_fill_rate > 0.70

        if sharpe_improved:
            if fill_rate_low:
                self._nudge_gamma(-1)
                self._nudge_edge(-1)
                direction_note = "sharpe_up_fill_low_tighten"
            else:
                self._nudge_gamma(self._last_gamma_direction if self._last_gamma_direction != 0 else -1)
                direction_note = "sharpe_up_reinforce"
        else:
            reverse = -self._last_gamma_direction if self._last_gamma_direction != 0 else 1
            self._nudge_gamma(reverse)
            if fill_rate_high:
                self._nudge_edge(+1)
                direction_note = "sharpe_down_fill_high_widen"
            elif fill_rate_low:
                self._nudge_edge(-1)
                direction_note = "sharpe_down_fill_low_loosen"
            else:
                direction_note = "sharpe_down_reverse_gamma"

        ofi_regime = analytics.get("regime_pnl", {})
        trending_pnl = ofi_regime.get("trend", {}).get("avg_usd", 0.0)
        if trending_pnl > 0:
            self.ofi_skew_bps = min(self.max_ofi_skew, self.ofi_skew_bps * (1 + self.step_size))
        elif trending_pnl < 0:
            self.ofi_skew_bps = max(self.min_ofi_skew, self.ofi_skew_bps * (1 - self.step_size))

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
