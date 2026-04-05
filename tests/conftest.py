from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from btc_hft.config import Settings


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        paper=True,
        symbol="BTC/USD",
        enable_shorts=False,
        dry_run=False,
        dashboard_enabled=False,
        dashboard_interval_seconds=2,
        session_start_utc=time(0, 0),
        session_end_utc=time(23, 59),
        max_trades_per_session=100,
        order_reprice_seconds=10.0,
        funding_rate_bps_per_hour=0.0,
        market_maker_target_spread_bps=3.0,
        market_maker_inventory_skew_bps=3.0,
        market_maker_size_skew_factor=0.75,
        market_maker_reprice_bps=1.0,
        min_net_edge_bps=12.0,
        spread_vol_factor=0.4,
        spread_inventory_factor=1.2,
        spread_ofi_factor=0.8,
        spread_min_bps=1.5,
        spread_max_bps=25.0,
        as_gamma=0.1,
        as_kappa=1.5,
        as_momentum_factor=0.3,
        ofi_window=50,
        ofi_skew_bps=1.5,
        ofi_signal_threshold=0.3,
        bayes_toxic_prior=0.2,
        bayes_toxic_threshold=0.70,
        bayes_update_strength=0.15,
        queue_fill_fast_ms=800.0,
        queue_fill_slow_ms=4000.0,
        self_cal_enabled=True,
        self_cal_every_n_fills=500,
        self_cal_step_size=0.05,
        self_cal_max_gamma=0.5,
        self_cal_min_gamma=0.02,
        analytics_window=300,
        ewma_vol_span=20,
        loop_interval_seconds=1.0,
        stale_data_seconds=10,
        max_position_btc=1.0,
        max_trade_notional_usd=1000.0,
        max_daily_loss_usd=100.0,
        max_consecutive_losses=5,
        cooldown_seconds=10,
        momentum_lookback_ticks=5,
        spread_bps_min=1.5,
        take_profit_bps=4.0,
        stop_loss_bps=3.0,
        max_holding_seconds=30,
        order_size_btc=0.1,
        order_price_offset_bps=0.5,
        db_path=Path("runtime/trades.db"),
        log_level="INFO",
    )
