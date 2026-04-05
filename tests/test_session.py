from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone, timedelta

from btc_hft.models import RuntimeState
from btc_hft.session import SessionGuard


def test_session_guard_resets_daily_and_stops_outside_window(settings):
    guard = SessionGuard(settings)
    state = RuntimeState()
    state.trading_day_utc = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    state.daily_trade_count = 7
    state.daily_realized_pnl_usd = -12
    state.daily_estimated_fees_usd = 1
    state.daily_estimated_slippage_usd = 1
    state.daily_funding_pnl_usd = 0

    now = datetime.now(timezone.utc)
    decision = guard.evaluate(state, now)
    assert decision.reset_daily is True
    assert state.daily_trade_count == 0

    later_start = (now + timedelta(hours=1)).time().replace(microsecond=0)
    later_end = (now + timedelta(hours=2)).time().replace(microsecond=0)
    guard = SessionGuard(replace(settings, session_start_utc=later_start, session_end_utc=later_end))
    decision = guard.evaluate(state, now)
    assert decision.should_stop is True
    assert decision.reason == "outside_session_window"
