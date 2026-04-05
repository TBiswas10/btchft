from __future__ import annotations

from btc_hft.market_maker import AlwaysOnMarketMaker
from btc_hft.models import PositionState, QuoteSnapshot


def test_market_maker_quotes_both_sides_and_skews_with_inventory(settings):
    maker = AlwaysOnMarketMaker(settings)
    quote = QuoteSnapshot(bid=100.0, ask=100.5)

    flat_plan = maker.build_plan(quote, PositionState())
    assert flat_plan is not None
    assert flat_plan.bid_price < quote.mid
    assert flat_plan.ask_price > quote.mid
    assert flat_plan.bid_qty > 0
    assert flat_plan.ask_qty > 0

    long_plan = maker.build_plan(quote, PositionState(qty_btc=0.5, avg_entry_price=100.0))
    assert long_plan is not None
    assert long_plan.bid_price < flat_plan.bid_price
    assert long_plan.ask_price < flat_plan.ask_price
    assert long_plan.ask_qty > long_plan.bid_qty
