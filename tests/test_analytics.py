from __future__ import annotations

from btc_hft.analytics import PerformanceAnalytics


def test_analytics_sharpe_zero_below_10_fills() -> None:
    a = PerformanceAnalytics(window=100)
    for _ in range(5):
        a.record_fill(0.01, 0.5, 0.0, "normal", 2.0, "unknown", "buy")
    assert a.sharpe == 0.0


def test_analytics_win_rate_correct() -> None:
    a = PerformanceAnalytics(window=100)
    for i in range(10):
        pnl = 0.01 if i < 7 else -0.01
        a.record_fill(pnl, 0.5, 0.0, "normal", 2.0, "unknown", "buy")
    assert abs(a.win_rate - 0.7) < 0.01


def test_analytics_snapshot_has_all_keys() -> None:
    a = PerformanceAnalytics(window=100)
    snap = a.snapshot()
    for key in ("sharpe", "win_rate", "fill_rate", "total_fills", "edge_blocks", "regime_pnl", "queue_pnl"):
        assert key in snap
