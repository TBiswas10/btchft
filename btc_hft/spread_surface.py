"""
Spread surface model.

Computes the bid-ask spread as a continuous function of:
  - realised volatility (wider spread in high vol)
  - inventory ratio (wider spread when inventory is extreme)
  - OFI signal (tighter on the informed side)
  - regime (multiplier per regime)
  - queue depth inference (tighter when at front of queue)

This replaces the fixed MARKET_MAKER_TARGET_SPREAD_BPS parameter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SpreadSurfaceOutput:
    half_spread_bps: float
    bid_offset_bps: float
    ask_offset_bps: float
    effective_spread_bps: float
    surface_inputs: dict


class SpreadSurface:
    """
    Computes optimal spread as a function of market state.

    The spread surface is:
      base_spread = AS_spread(gamma, kappa, sigma)
      vol_term    = vol_bps * vol_factor
      inv_term    = abs(inventory_ratio)^1.5 * inventory_factor * base_spread
      ofi_term    = abs(ofi_score) * ofi_factor  (tightens informed side)
      regime_mult = {quiet: 0.8, normal: 1.0, trend: 1.25, high_vol: 1.5}
      queue_mult  = {front: 0.9, back: 1.1, unknown: 1.0}
    """

    REGIME_MULTIPLIERS = {
        "quiet": 0.80,
        "normal": 1.00,
        "trend": 1.25,
        "high_vol": 1.55,
        "warmup": 1.00,
        "unknown": 1.00,
    }

    QUEUE_MULTIPLIERS = {
        "front": 0.90,
        "back": 1.10,
        "unknown": 1.00,
    }

    def __init__(
        self,
        as_gamma: float = 0.1,
        as_kappa: float = 1.5,
        vol_factor: float = 0.4,
        inventory_factor: float = 1.2,
        ofi_factor: float = 0.8,
        min_bps: float = 1.5,
        max_bps: float = 25.0,
    ) -> None:
        self.as_gamma = as_gamma
        self.as_kappa = as_kappa
        self.vol_factor = vol_factor
        self.inventory_factor = inventory_factor
        self.ofi_factor = ofi_factor
        self.min_bps = min_bps
        self.max_bps = max_bps

    def _as_base_spread(self, sigma_bps: float) -> float:
        """
        Avellaneda-Stoikov optimal spread formula.
        spread* = gamma * sigma^2 + (2/gamma) * ln(1 + gamma/kappa)
        Computed in bps units with sigma already in bps.
        """
        sigma = max(sigma_bps, 0.5)
        term1 = self.as_gamma * (sigma ** 2) / 100.0
        term2 = (2.0 / self.as_gamma) * math.log(1.0 + self.as_gamma / self.as_kappa)
        return max(term1 + term2, self.min_bps)

    def compute(
        self,
        vol_bps: float,
        inventory_ratio: float,
        ofi_score: float,
        regime: str,
        queue_position: str = "unknown",
        momentum_bps: float = 0.0,
    ) -> SpreadSurfaceOutput:
        """
        Compute the full spread surface output.
        """
        base = self._as_base_spread(vol_bps)
        vol_term = vol_bps * self.vol_factor
        inv_abs = abs(inventory_ratio)
        inv_term = (inv_abs ** 1.5) * self.inventory_factor * base
        raw_spread = base + vol_term + inv_term
        regime_mult = self.REGIME_MULTIPLIERS.get(regime, 1.0)
        queue_mult = self.QUEUE_MULTIPLIERS.get(queue_position, 1.0)
        total_spread = raw_spread * regime_mult * queue_mult
        total_spread = max(self.min_bps, min(self.max_bps, total_spread))
        half_spread = total_spread / 2.0

        ofi_adjustment = ofi_score * self.ofi_factor
        momentum_adjustment = momentum_bps * 0.1
        bid_offset = ofi_adjustment - momentum_adjustment
        ask_offset = -ofi_adjustment + momentum_adjustment

        return SpreadSurfaceOutput(
            half_spread_bps=half_spread,
            bid_offset_bps=bid_offset,
            ask_offset_bps=ask_offset,
            effective_spread_bps=total_spread,
            surface_inputs={
                "base_as_spread": round(base, 3),
                "vol_term": round(vol_term, 3),
                "inv_term": round(inv_term, 3),
                "regime_mult": regime_mult,
                "queue_mult": queue_mult,
                "ofi_adjustment": round(ofi_adjustment, 3),
                "momentum_adjustment": round(momentum_adjustment, 3),
                "vol_bps": round(vol_bps, 3),
                "inventory_ratio": round(inventory_ratio, 3),
                "ofi_score": round(ofi_score, 3),
                "regime": regime,
                "queue_position": queue_position,
            },
        )
