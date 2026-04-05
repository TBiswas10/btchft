from __future__ import annotations

from datetime import datetime, timedelta, timezone

from btc_hft.models import PositionState, RuntimeState
from btc_hft.portfolio import apply_fill_to_state, apply_funding_to_state, mark_to_market_unrealized_pnl


def test_apply_fill_and_funding_updates_state():
    state = RuntimeState(position=PositionState())
    impact = apply_fill_to_state(state, "buy", 1.0, 101.0, 100.0, 0.001)

    assert state.position.qty_btc == 1.0
    assert state.realized_pnl_usd == 0.0
    assert state.estimated_fees_usd == 0.101
    assert state.estimated_slippage_usd == 1.0
    assert state.daily_trade_count == 1
    assert impact.fee_usd == 0.101

    state.last_funding_applied_at = datetime.now(timezone.utc) - timedelta(hours=1)
    funding = apply_funding_to_state(state, 100.0, 10.0)
    assert funding < 0
    assert state.funding_pnl_usd == funding

    unrealized = mark_to_market_unrealized_pnl(state, 102.0)
    assert unrealized == 1.0
