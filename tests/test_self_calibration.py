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
