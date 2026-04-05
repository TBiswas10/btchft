from __future__ import annotations

from datetime import datetime, timezone

from btc_hft.alerts import AlertConfig, AlertDispatcher
from btc_hft.auto_ops import AutoOpsGuard
from btc_hft.experiments import (
    ChampionChallengerWeeklySweep,
    ParamSet,
    WeeklyMetric,
)
from btc_hft.market_neutral import SimpleHedgeArbitrage, VenueQuote


def test_market_neutral_finds_edge_primary_to_hedge() -> None:
    strat = SimpleHedgeArbitrage(max_abs_position_btc=0.02, max_leg_qty_btc=0.005, min_edge_bps=1.0)
    primary = VenueQuote("alpaca", bid=40000.0, ask=40001.0)
    hedge = VenueQuote("coinbase", bid=40010.0, ask=40011.0)

    signal = strat.evaluate(primary, hedge, current_net_position_btc=0.0)

    assert signal.should_trade
    assert signal.buy_venue == "alpaca"
    assert signal.sell_venue == "coinbase"
    assert signal.qty_btc == 0.005


def test_market_neutral_respects_position_cap() -> None:
    strat = SimpleHedgeArbitrage(max_abs_position_btc=0.01, max_leg_qty_btc=0.005, min_edge_bps=1.0)
    primary = VenueQuote("alpaca", bid=40000.0, ask=40001.0)
    hedge = VenueQuote("coinbase", bid=40010.0, ask=40011.0)

    signal = strat.evaluate(primary, hedge, current_net_position_btc=0.01)

    assert not signal.should_trade
    assert signal.reason == "position_cap_reached"


def test_auto_ops_stale_feed_and_slippage() -> None:
    guard = AutoOpsGuard(stale_data_seconds=10, max_fill_slippage_usd=1.5)

    stale = guard.check_health(data_age_seconds=11.0, latest_stream_health={"connected": True})
    assert stale.should_stop
    assert stale.reason == "auto_stop_stale_feed"

    slippage = guard.check_fill_slippage(2.0)
    assert slippage.should_stop
    assert slippage.reason == "auto_stop_abnormal_slippage"


def test_stream_issue_recovery_reason_is_reconnectable() -> None:
    assert AutoOpsGuard.is_recoverable_stream_issue("auto_stop_stale_feed")
    assert AutoOpsGuard.is_recoverable_stream_issue("auto_stop_stream_disconnected")
    assert not AutoOpsGuard.is_recoverable_stream_issue("auto_stop_abnormal_slippage")


def test_auto_ops_does_not_stop_before_first_stream_message() -> None:
    guard = AutoOpsGuard(stale_data_seconds=10, max_fill_slippage_usd=1.5)
    decision = guard.check_health(
        data_age_seconds=999.0,
        latest_stream_health={
            "connected": True,
            "last_message_at": None,
            "data_age_seconds": None,
        },
    )
    assert not decision.should_stop


def test_auto_ops_daily_report_once_per_day() -> None:
    guard = AutoOpsGuard(stale_data_seconds=10, max_fill_slippage_usd=1.0)
    now = datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc)

    assert guard.should_emit_daily_report(now)
    assert not guard.should_emit_daily_report(now)


def test_weekly_sweep_selects_better_score() -> None:
    sweep = ChampionChallengerWeeklySweep()
    champion = ParamSet("champion", 8.0, 3.0, 1.0)
    challenger = ParamSet("challenger", 7.0, 2.5, 0.8)

    champ_metric = WeeklyMetric(realized_pnl_usd=100.0, max_drawdown_usd=50.0, win_rate_pct=52.0, slippage_usd=15.0, trade_count=60)
    chall_metric = WeeklyMetric(realized_pnl_usd=120.0, max_drawdown_usd=45.0, win_rate_pct=53.0, slippage_usd=13.0, trade_count=60)

    result = sweep.run(champion, challenger, champ_metric, chall_metric)

    assert result.winner_name == "challenger"


def test_alert_dispatcher_disabled_channel_noop() -> None:
    dispatcher = AlertDispatcher(AlertConfig(channel="disabled"))
    sent = dispatcher.send("title", "message")
    assert not sent
