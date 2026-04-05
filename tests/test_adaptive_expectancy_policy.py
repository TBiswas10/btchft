from __future__ import annotations

from btc_hft.adaptive_expectancy_policy import AdaptiveExpectancyPolicy, DecisionInput


def _inp(**overrides) -> DecisionInput:
    base = DecisionInput(
        expected_capture_bps=1.0,
        spread_half_bps=0.5,
        ofi_score=0.2,
        momentum_bps=1.0,
        regime="normal",
        queue_position="front",
        inventory_ratio=0.0,
        estimated_fill_prob=0.7,
        adverse_selection_bps=0.1,
        fee_bps=0.08,
        slippage_bps=0.08,
        uncertainty=0.2,
        toxicity_prob=0.2,
        quote_notional_usd=500.0,
    )
    return DecisionInput(**{**base.__dict__, **overrides})


def test_flat_market_low_signal_is_suppressed() -> None:
    p = AdaptiveExpectancyPolicy(base_threshold_bps=0.1, min_confidence=0.1)
    d = p.evaluate(
        _inp(
            regime="quiet",
            ofi_score=0.0,
            momentum_bps=0.0,
            queue_position="back",
            estimated_fill_prob=0.25,
            expected_capture_bps=0.2,
            uncertainty=0.8,
        )
    )
    assert not d.should_trade
    assert d.reason in {
        "low_information_signal_suppressed",
        "low_signal_confidence",
        "quiet_regime_hold_for_stronger_edge",
    }


def test_positive_edge_normal_regime_selective_trade() -> None:
    p = AdaptiveExpectancyPolicy(base_threshold_bps=0.1, min_confidence=0.1)
    d = p.evaluate(
        _inp(
            regime="normal",
            ofi_score=0.8,
            momentum_bps=4.0,
            expected_capture_bps=3.0,
            estimated_fill_prob=0.85,
            uncertainty=0.1,
            toxicity_prob=0.05,
        )
    )
    assert d.should_trade
    assert d.expected_net_bps > d.threshold_used


def test_toxic_regime_reduces_or_blocks_trading() -> None:
    p = AdaptiveExpectancyPolicy(base_threshold_bps=0.1, min_confidence=0.1)
    d = p.evaluate(
        _inp(
            regime="high_vol",
            ofi_score=0.5,
            momentum_bps=3.0,
            expected_capture_bps=1.8,
            estimated_fill_prob=0.55,
            toxicity_prob=0.95,
            uncertainty=0.9,
        )
    )
    assert (not d.should_trade) or d.size_multiplier < 1.0 or d.spread_multiplier > 1.0


def test_normal_regime_low_fill_uses_fill_assist() -> None:
    p = AdaptiveExpectancyPolicy(base_threshold_bps=0.1, min_confidence=0.1)
    d = p.evaluate(
        _inp(
            regime="normal",
            queue_position="unknown",
            ofi_score=0.9,
            momentum_bps=5.0,
            expected_capture_bps=3.5,
            estimated_fill_prob=0.45,
            uncertainty=0.1,
            toxicity_prob=0.05,
        )
    )
    assert d.should_trade
    assert d.reason in {"post_cost_expectancy_ok", "post_cost_expectancy_ok_normal_fill_assist"}
    if d.reason == "post_cost_expectancy_ok_normal_fill_assist":
        assert d.spread_multiplier < 1.0


def test_normal_regime_very_low_fill_can_be_blocked() -> None:
    p = AdaptiveExpectancyPolicy(base_threshold_bps=0.1, min_confidence=0.1)
    d = p.evaluate(
        _inp(
            regime="normal",
            queue_position="back",
            ofi_score=0.35,
            momentum_bps=1.4,
            expected_capture_bps=2.0,
            estimated_fill_prob=0.30,
            uncertainty=0.2,
            toxicity_prob=0.10,
        )
    )
    assert not d.should_trade
    assert d.reason in {
        "normal_low_fill_soft_block",
        "post_cost_expectancy_below_threshold",
        "soft_gate_reduce_size_widen",
    }
