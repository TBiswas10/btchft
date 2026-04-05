from __future__ import annotations

from pathlib import Path

from btc_hft.decision_policy import (
    DecisionInput,
    DecisionOutcome,
    ExpectancyDecisionPolicy,
    TradeDecision,
    calibrate_policy_from_outcomes,
)


def _sample_input(**overrides) -> DecisionInput:
    base = DecisionInput(
        expected_capture_bps=1.2,
        spread_half_bps=0.6,
        ofi_score=0.4,
        momentum_bps=2.0,
        regime="normal",
        queue_position="front",
        inventory_ratio=0.1,
        estimated_fill_prob=0.65,
        adverse_selection_bps=0.15,
        fee_bps=0.08,
        slippage_bps=0.06,
        uncertainty=0.2,
        toxicity_prob=0.15,
    )
    return DecisionInput(**{**base.__dict__, **overrides})


def test_no_trade_in_flat_low_information_market() -> None:
    policy = ExpectancyDecisionPolicy(base_threshold_bps=0.2, min_confidence=0.25)
    decision = policy.evaluate(
        _sample_input(
            expected_capture_bps=0.1,
            ofi_score=0.0,
            momentum_bps=0.0,
            uncertainty=0.95,
            queue_position="back",
            estimated_fill_prob=0.15,
        )
    )
    assert not decision.should_trade
    assert decision.reason in {"low_signal_confidence", "post_cost_expectancy_below_threshold", "toxic_or_high_vol_block"}


def test_trade_only_when_post_cost_expectancy_positive() -> None:
    policy = ExpectancyDecisionPolicy(base_threshold_bps=0.1, min_confidence=0.1)
    decision = policy.evaluate(_sample_input(expected_capture_bps=2.2, estimated_fill_prob=0.8, uncertainty=0.1))
    assert decision.should_trade
    assert decision.expected_net_bps > decision.threshold_used


def test_reduced_activity_in_toxic_high_vol_regime() -> None:
    policy = ExpectancyDecisionPolicy(base_threshold_bps=0.1, min_confidence=0.1)
    quiet = policy.evaluate(_sample_input(regime="quiet", toxicity_prob=0.1, uncertainty=0.1, expected_capture_bps=1.6))
    toxic = policy.evaluate(_sample_input(regime="high_vol", toxicity_prob=0.9, uncertainty=0.9, expected_capture_bps=1.6))
    assert quiet.should_trade or quiet.reason == "post_cost_expectancy_below_threshold"
    assert not toxic.should_trade


def test_calibration_artifact_walk_forward_and_consistent_decisions(tmp_path: Path) -> None:
    outcomes = []
    for i in range(40):
        outcomes.append(
            DecisionOutcome(
                regime="normal" if i < 20 else "high_vol",
                queue_position="front" if i % 2 == 0 else "unknown",
                expected_net_bps=0.6 - (0.03 * (i % 5)),
                realized_net_bps=0.25 - (0.02 * (i % 4)),
                expected_capture_bps=1.0,
                fill_prob=0.6,
                confidence=0.7,
                fee_bps=0.08,
                slippage_bps=0.10,
                adverse_selection_bps=0.12,
            )
        )

    artifact = calibrate_policy_from_outcomes(outcomes, output_dir=tmp_path)
    assert artifact.version.startswith("expectancy_")
    files = list(tmp_path.glob("expectancy_*.json"))
    assert files

    policy = ExpectancyDecisionPolicy(base_threshold_bps=0.1)
    policy.apply_artifact(artifact)
    inp = _sample_input(regime="normal", expected_capture_bps=1.8)
    d1: TradeDecision = policy.evaluate(inp)
    d2: TradeDecision = policy.evaluate(inp)
    assert d1 == d2
