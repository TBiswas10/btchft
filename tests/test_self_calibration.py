from __future__ import annotations

from btc_hft.self_calibration import SelfCalibrator


def test_calibrator_nudges_gamma_on_worsening_sharpe() -> None:
    cal = SelfCalibrator(initial_gamma=0.1, step_size=0.1)
    cal._run_calibration({"sharpe": 1.0, "fill_rate": 0.5, "regime_pnl": {}})
    gamma_after_first = cal.as_gamma
    cal._run_calibration({"sharpe": 0.5, "fill_rate": 0.5, "regime_pnl": {}})
    assert cal.as_gamma != gamma_after_first


def test_calibrator_stays_within_bounds() -> None:
    cal = SelfCalibrator(initial_gamma=0.1, min_gamma=0.02, max_gamma=0.5, step_size=0.5)
    for _ in range(50):
        cal._run_calibration({"sharpe": -1.0, "fill_rate": 0.9, "regime_pnl": {}})
    assert cal.min_gamma <= cal.as_gamma <= cal.max_gamma


def test_calibrator_prioritizes_positive_expectancy_over_fill_rate() -> None:
    cal = SelfCalibrator(initial_min_edge_bps=1.5, step_size=0.1)
    cal._run_calibration({"sharpe": 0.2, "fill_rate": 0.08, "rolling_post_cost_expectancy_bps": 0.12, "expectancy_ci_low_bps": 0.05, "regime_expectancy_bps": {"trend": 0.1, "high_vol": 0.02}})
    edge_after_first = cal.min_net_edge_bps
    cal._run_calibration({"sharpe": 0.3, "fill_rate": 0.07, "rolling_post_cost_expectancy_bps": 0.14, "expectancy_ci_low_bps": 0.06, "regime_expectancy_bps": {"trend": 0.11, "high_vol": 0.03}})
    assert cal.min_net_edge_bps <= edge_after_first
