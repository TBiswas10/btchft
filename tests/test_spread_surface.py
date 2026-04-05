from __future__ import annotations

from btc_hft.spread_surface import SpreadSurface


def test_spread_surface_wider_in_high_vol() -> None:
    ss = SpreadSurface(as_gamma=0.1, as_kappa=1.5)
    low = ss.compute(vol_bps=1.0, inventory_ratio=0.0, ofi_score=0.0, regime="quiet")
    high = ss.compute(vol_bps=20.0, inventory_ratio=0.0, ofi_score=0.0, regime="high_vol")
    assert high.effective_spread_bps > low.effective_spread_bps


def test_spread_surface_clamped_to_max() -> None:
    ss = SpreadSurface(max_bps=10.0)
    out = ss.compute(vol_bps=500.0, inventory_ratio=1.0, ofi_score=0.0, regime="high_vol")
    assert out.effective_spread_bps <= 10.0


def test_spread_surface_ofi_adjusts_asymmetry() -> None:
    ss = SpreadSurface()
    bullish = ss.compute(vol_bps=5.0, inventory_ratio=0.0, ofi_score=0.8, regime="normal")
    bearish = ss.compute(vol_bps=5.0, inventory_ratio=0.0, ofi_score=-0.8, regime="normal")
    assert bullish.ask_offset_bps < bearish.ask_offset_bps
