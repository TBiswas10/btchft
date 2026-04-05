from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .models import PositionState, QuoteSnapshot


@dataclass(frozen=True)
class QuotePlan:
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    half_spread_bps: float
    inventory_ratio: float
    quote_mid: float


class AlwaysOnMarketMaker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
    ) -> QuotePlan | None:
        if quote.mid <= 0:
            return None

        inventory_ratio = 0.0
        if self.settings.max_position_btc > 0:
            inventory_ratio = self._clamp(position.qty_btc / self.settings.max_position_btc, -1.0, 1.0)

        target_spread_bps = max(self.settings.market_maker_target_spread_bps, self.settings.spread_bps_min)
        target_spread_bps += max(volatility_bps, 0.0) * 0.5
        if regime == "high_vol":
            target_spread_bps *= 1.4
        elif regime == "trend":
            target_spread_bps *= 1.2
        elif regime == "quiet":
            target_spread_bps *= 0.9

        target_spread_bps *= max(spread_multiplier, 0.25)
        half_spread_bps = max(target_spread_bps / 2.0, 0.5)

        skew_bps = inventory_ratio * (self.settings.market_maker_inventory_skew_bps + max(extra_inventory_skew_bps, 0.0))
        bid_bps = half_spread_bps + skew_bps
        ask_bps = half_spread_bps - skew_bps

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
            half_spread_bps=half_spread_bps,
            inventory_ratio=inventory_ratio,
            quote_mid=quote.mid,
        )
