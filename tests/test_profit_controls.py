from __future__ import annotations

from datetime import datetime, timedelta, timezone

from btc_hft.profit_controls import (
    AdverseSelectionGuard,
    ExecutionQualityMonitor,
    NetEdgeGate,
    RegimeDetector,
    build_pnl_attribution,
)


def test_net_edge_gate_blocks_when_costs_too_high() -> None:
    gate = NetEdgeGate(min_net_edge_bps=0.5)
    decision = gate.evaluate(
        expected_edge_bps=1.0,
        fee_bps=0.4,
        slippage_bps=0.2,
        adverse_selection_bps=0.1,
    )
    assert not decision.should_trade
    assert decision.reason == "net_edge_below_threshold"


def test_net_edge_gate_allows_positive_net_edge() -> None:
    gate = NetEdgeGate(min_net_edge_bps=0.2)
    decision = gate.evaluate(
        expected_edge_bps=1.2,
        fee_bps=0.4,
        slippage_bps=0.2,
        adverse_selection_bps=0.1,
    )
    assert decision.should_trade


def test_regime_detector_high_vol() -> None:
    det = RegimeDetector(lookback=20)
    mids = [100, 100.1, 99.8, 100.4, 99.7, 100.5, 99.6, 100.4, 99.5, 100.6]
    out = None
    for m in mids:
        out = det.update(float(m))
    assert out is not None
    assert out.regime in {"high_vol", "trend", "normal", "quiet"}


def test_adverse_selection_guard_pauses_after_spike() -> None:
    g = AdverseSelectionGuard(move_bps_threshold=5.0, cooldown_seconds=2)
    now = datetime.now(timezone.utc)

    paused, _ = g.update_and_check(100.0, now)
    assert not paused

    paused, reason = g.update_and_check(100.2, now + timedelta(milliseconds=100))
    assert paused
    assert reason in {"adverse_selection_spike", "adverse_selection_cooldown"}


def test_execution_quality_derisk_trigger() -> None:
    m = ExecutionQualityMonitor()
    for _ in range(20):
        m.on_submitted()
    for _ in range(1):
        m.on_fill(0.1)
    for _ in range(8):
        m.on_rejected()

    derisk, reason, metrics = m.should_derisk(
        min_fill_ratio=0.2,
        max_reject_ratio=0.2,
        max_avg_slippage_usd=1.0,
    )
    assert derisk
    assert reason in {"low_fill_ratio", "high_reject_ratio", "high_avg_slippage"}
    assert metrics.submitted == 20


def test_pnl_attribution_fields() -> None:
    a = build_pnl_attribution(realized_usd=10.0, fees_usd=1.0, slippage_usd=0.5, funding_usd=0.2)
    assert a.realized_usd == 10.0
    assert a.fees_usd == 1.0
    assert a.slippage_usd == 0.5
    assert a.funding_usd == 0.2
