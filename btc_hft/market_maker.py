from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .models import PositionState, QuoteSnapshot
from .spread_surface import SpreadSurface


@dataclass(frozen=True)
class QuotePlan:
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    half_spread_bps: float
    inventory_ratio: float
    quote_mid: float
    surface_inputs: dict = field(default_factory=dict)
    momentum_bps: float = 0.0
    queue_position: str = "unknown"


class AlwaysOnMarketMaker:
    def __init__(self, settings: Settings, spread_surface: SpreadSurface | None = None) -> None:
        self.settings = settings
        self.spread_surface = spread_surface or SpreadSurface(
            as_gamma=getattr(settings, "as_gamma", 0.1),
            as_kappa=getattr(settings, "as_kappa", 1.5),
            vol_factor=getattr(settings, "spread_vol_factor", 0.4),
            inventory_factor=getattr(settings, "spread_inventory_factor", 1.2),
            ofi_factor=getattr(settings, "spread_ofi_factor", 0.8),
            min_bps=getattr(settings, "spread_min_bps", 1.5),
            max_bps=getattr(settings, "spread_max_bps", 25.0),
        )

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(value, upper))

    def build_plan(
        self,
        quote: QuoteSnapshot,
        position: PositionState,
        volatility_bps: float = 0.0,
        regime: str = "normal",
        spread_multiplier: float = 1.0,
        size_multiplier: float = 1.0,
        extra_inventory_skew_bps: float = 0.0,
        ofi_score: float = 0.0,
        momentum_bps: float = 0.0,
        queue_position: str = "unknown",
    ) -> QuotePlan | None:
        if quote.mid <= 0:
            return None

        inventory_ratio = 0.0
        if self.settings.max_position_btc > 0:
            inventory_ratio = self._clamp(position.qty_btc / self.settings.max_position_btc, -1.0, 1.0)

        surface = self.spread_surface.compute(
            vol_bps=max(volatility_bps, 0.0),
            inventory_ratio=inventory_ratio,
            ofi_score=ofi_score,
            regime=regime,
            queue_position=queue_position,
            momentum_bps=momentum_bps,
        )

        half_spread = surface.half_spread_bps * max(spread_multiplier, 0.25)
        bid_bps = half_spread + surface.bid_offset_bps
        ask_bps = half_spread + surface.ask_offset_bps

        skew_bps = inventory_ratio * (
            self.settings.market_maker_inventory_skew_bps + max(extra_inventory_skew_bps, 0.0)
        )
        bid_bps += skew_bps
        ask_bps -= skew_bps

        bid_bps = max(bid_bps, 0.25)
        ask_bps = max(ask_bps, 0.25)

        bid_price = quote.mid * (1 - bid_bps / 10000.0)
        ask_price = quote.mid * (1 + ask_bps / 10000.0)

        base_size = self.settings.order_size_btc * max(size_multiplier, 0.1)
        buy_size_bias = self._clamp(1.0 - inventory_ratio * self.settings.market_maker_size_skew_factor, 0.25, 2.5)
        sell_size_bias = self._clamp(1.0 + inventory_ratio * self.settings.market_maker_size_skew_factor, 0.25, 2.5)

        return QuotePlan(
            bid_price=bid_price,
            bid_qty=base_size * buy_size_bias,
            ask_price=ask_price,
            ask_qty=base_size * sell_size_bias,
            half_spread_bps=half_spread,
            inventory_ratio=inventory_ratio,
            quote_mid=quote.mid,
            surface_inputs=surface.surface_inputs,
            momentum_bps=momentum_bps,
            queue_position=queue_position,
        )
