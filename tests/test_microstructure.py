from __future__ import annotations

import time

from btc_hft.microstructure import (
    BayesianRegimeDetector,
    EWMAVolatility,
    MicrostructureEngine,
    OrderFlowImbalance,
    QueuePositionInference,
)


def test_ewma_volatility_first_tick_zero() -> None:
    vol = EWMAVolatility(span=10)
    assert vol.update(50000.0) == 0.0


def test_ewma_volatility_grows_with_moves() -> None:
    vol = EWMAVolatility(span=10)
    vol.update(50000.0)
    vol.update(50100.0)
    v1 = vol.current_bps
    vol.update(49800.0)
    v2 = vol.current_bps
    assert v2 > v1


def test_ofi_empty_returns_zero() -> None:
    ofi = OrderFlowImbalance(window=20)
    assert ofi.score == 0.0


def test_ofi_rising_mids_positive_score() -> None:
    ofi = OrderFlowImbalance(window=20)
    for price in range(100, 120):
        ofi.update(float(price) - 0.5, float(price) + 0.5)
    assert ofi.score > 0.3
    assert ofi.is_bullish


def test_ofi_falling_mids_negative_score() -> None:
    ofi = OrderFlowImbalance(window=20)
    for price in range(120, 100, -1):
        ofi.update(float(price) - 0.5, float(price) + 0.5)
    assert ofi.score < -0.3
    assert ofi.is_bearish


def test_bayesian_detector_updates_on_adverse() -> None:
    det = BayesianRegimeDetector(prior_toxic=0.2, toxic_threshold=0.70)
    initial_p = det.p_toxic
    for _ in range(20):
        det.update_on_fill("buy", 50000.0, 49500.0)
    assert det.p_toxic > initial_p


def test_bayesian_detector_decays_toward_prior() -> None:
    det = BayesianRegimeDetector(prior_toxic=0.2)
    det.p_toxic = 0.9
    for _ in range(100):
        det.decay_toward_prior(prior_toxic=0.2)
    assert det.p_toxic < 0.9


def test_queue_position_unknown_initially() -> None:
    q = QueuePositionInference()
    assert q.position == "unknown"


def test_queue_position_front_on_fast_fills() -> None:
    q = QueuePositionInference(fast_threshold_ms=1000, slow_threshold_ms=5000)
    for _ in range(10):
        q.on_order_submitted()
        time.sleep(0.001)
        q.on_fill()
    assert q.position == "front"


def test_microstructure_engine_snapshot_fields() -> None:
    engine = MicrostructureEngine()
    snap = engine.update(50000.0, 50010.0)
    assert hasattr(snap, "ofi_score")
    assert hasattr(snap, "vol_bps")
    assert hasattr(snap, "momentum")
    assert hasattr(snap, "queue_position")
    assert hasattr(snap, "bayes_p_toxic")
    assert hasattr(snap, "should_liquidate")
    assert isinstance(snap.ofi_score, float)
    assert -1.0 <= snap.ofi_score <= 1.0
