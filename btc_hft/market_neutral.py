from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VenueQuote:
    venue: str
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        if self.bid <= 0 or self.ask <= 0:
            return 0.0
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class HedgeSignal:
    should_trade: bool
    reason: str
    buy_venue: str | None = None
    sell_venue: str | None = None
    qty_btc: float = 0.0
    expected_edge_bps: float = 0.0


class SimpleHedgeArbitrage:
    """Single-path market-neutral hedge/arbitrage strategy with hard caps."""

    def __init__(
        self,
        max_abs_position_btc: float,
        max_leg_qty_btc: float,
        min_edge_bps: float,
    ) -> None:
        self.max_abs_position_btc = max(0.0, max_abs_position_btc)
        self.max_leg_qty_btc = max(0.0, max_leg_qty_btc)
        self.min_edge_bps = max(0.0, min_edge_bps)

    def evaluate(
        self,
        primary: VenueQuote,
        hedge: VenueQuote,
        current_net_position_btc: float,
    ) -> HedgeSignal:
        if primary.ask <= 0 or primary.bid <= 0 or hedge.ask <= 0 or hedge.bid <= 0:
            return HedgeSignal(False, "invalid_quote")

        if abs(current_net_position_btc) >= self.max_abs_position_btc:
            return HedgeSignal(False, "position_cap_reached")

        cap_room = self.max_abs_position_btc - abs(current_net_position_btc)
        qty = min(self.max_leg_qty_btc, cap_room)
        if qty <= 0:
            return HedgeSignal(False, "no_capacity")

        # Path A: buy primary at ask, sell hedge at bid.
        edge_a = ((hedge.bid - primary.ask) / max(primary.ask, 1e-9)) * 10000.0
        # Path B: buy hedge at ask, sell primary at bid.
        edge_b = ((primary.bid - hedge.ask) / max(hedge.ask, 1e-9)) * 10000.0

        if edge_a >= edge_b and edge_a >= self.min_edge_bps:
            return HedgeSignal(
                should_trade=True,
                reason="edge_primary_to_hedge",
                buy_venue=primary.venue,
                sell_venue=hedge.venue,
                qty_btc=qty,
                expected_edge_bps=edge_a,
            )

        if edge_b > edge_a and edge_b >= self.min_edge_bps:
            return HedgeSignal(
                should_trade=True,
                reason="edge_hedge_to_primary",
                buy_venue=hedge.venue,
                sell_venue=primary.venue,
                qty_btc=qty,
                expected_edge_bps=edge_b,
            )

        return HedgeSignal(False, "edge_below_threshold")
