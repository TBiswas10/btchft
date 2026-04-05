from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return float(value)


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _parse_utc_time(value: str) -> dtime:
    parts = value.strip().split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"Invalid UTC time '{value}'. Use HH:MM or HH:MM:SS.")
    hour = int(parts[0])
    minute = int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    return dtime(hour=hour, minute=minute, second=second)


@dataclass(frozen=True)
class Settings:
    alpaca_api_key: str
    alpaca_secret_key: str
    paper: bool
    symbol: str
    enable_shorts: bool
    dry_run: bool
    dashboard_enabled: bool
    dashboard_interval_seconds: int
    session_start_utc: dtime
    session_end_utc: dtime
    max_trades_per_session: int
    order_reprice_seconds: float
    funding_rate_bps_per_hour: float
    market_maker_target_spread_bps: float
    market_maker_inventory_skew_bps: float
    market_maker_size_skew_factor: float
    market_maker_reprice_bps: float
    min_net_edge_bps: float
    spread_vol_factor: float
    spread_inventory_factor: float
    spread_ofi_factor: float
    spread_min_bps: float
    spread_max_bps: float
    as_gamma: float
    as_kappa: float
    as_momentum_factor: float
    ofi_window: int
    ofi_skew_bps: float
    ofi_signal_threshold: float
    bayes_toxic_prior: float
    bayes_toxic_threshold: float
    bayes_update_strength: float
    queue_fill_fast_ms: float
    queue_fill_slow_ms: float
    self_cal_enabled: bool
    self_cal_every_n_fills: int
    self_cal_step_size: float
    self_cal_max_gamma: float
    self_cal_min_gamma: float
    analytics_window: int
    ewma_vol_span: int

    loop_interval_seconds: float
    stale_data_seconds: int

    max_position_btc: float
    max_trade_notional_usd: float
    max_daily_loss_usd: float
    max_consecutive_losses: int
    cooldown_seconds: int

    momentum_lookback_ticks: int
    spread_bps_min: float
    take_profit_bps: float
    stop_loss_bps: float
    max_holding_seconds: int

    order_size_btc: float
    order_price_offset_bps: float

    db_path: Path
    log_level: str


    @property
    def alpaca_base_url(self) -> str:
        return "https://paper-api.alpaca.markets" if self.paper else "https://api.alpaca.markets"

    @property
    def trading_symbol(self) -> str:
        return self.symbol.replace("/", "")


def validate_settings(settings: Settings) -> None:
    if not settings.paper:
        raise ValueError("This bot is configured for Alpaca paper trading only. Set ALPACA_PAPER=true.")

    if settings.dry_run and settings.enable_shorts:
        # allowed, but keep it explicit for now
        pass

    numeric_checks = {
        "LOOP_INTERVAL_SECONDS": settings.loop_interval_seconds,
        "STALE_DATA_SECONDS": settings.stale_data_seconds,
        "MAX_POSITION_BTC": settings.max_position_btc,
        "MAX_TRADE_NOTIONAL_USD": settings.max_trade_notional_usd,
        "MAX_DAILY_LOSS_USD": settings.max_daily_loss_usd,
        "MAX_CONSECUTIVE_LOSSES": float(settings.max_consecutive_losses),
        "COOLDOWN_SECONDS": float(settings.cooldown_seconds),
        "MOMENTUM_LOOKBACK_TICKS": float(settings.momentum_lookback_ticks),
        "SPREAD_BPS_MIN": settings.spread_bps_min,
        "TAKE_PROFIT_BPS": settings.take_profit_bps,
        "STOP_LOSS_BPS": settings.stop_loss_bps,
        "MAX_HOLDING_SECONDS": float(settings.max_holding_seconds),
        "ORDER_SIZE_BTC": settings.order_size_btc,
        "ORDER_PRICE_OFFSET_BPS": settings.order_price_offset_bps,
        "DASHBOARD_INTERVAL_SECONDS": float(settings.dashboard_interval_seconds),
        "MAX_TRADES_PER_SESSION": float(settings.max_trades_per_session),
        "ORDER_REPRICE_SECONDS": settings.order_reprice_seconds,
        "MARKET_MAKER_TARGET_SPREAD_BPS": settings.market_maker_target_spread_bps,
        "MARKET_MAKER_INVENTORY_SKEW_BPS": settings.market_maker_inventory_skew_bps,
        "MARKET_MAKER_SIZE_SKEW_FACTOR": settings.market_maker_size_skew_factor,
        "MARKET_MAKER_REPRICE_BPS": settings.market_maker_reprice_bps,
        "MIN_NET_EDGE_BPS": settings.min_net_edge_bps,
        "SPREAD_VOL_FACTOR": settings.spread_vol_factor,
        "SPREAD_INVENTORY_FACTOR": settings.spread_inventory_factor,
        "SPREAD_OFI_FACTOR": settings.spread_ofi_factor,
        "SPREAD_MIN_BPS": settings.spread_min_bps,
        "SPREAD_MAX_BPS": settings.spread_max_bps,
        "AS_GAMMA": settings.as_gamma,
        "AS_KAPPA": settings.as_kappa,
        "AS_MOMENTUM_FACTOR": settings.as_momentum_factor,
        "OFI_WINDOW": float(settings.ofi_window),
        "OFI_SKEW_BPS": settings.ofi_skew_bps,
        "OFI_SIGNAL_THRESHOLD": settings.ofi_signal_threshold,
        "BAYES_TOXIC_PRIOR": settings.bayes_toxic_prior,
        "BAYES_TOXIC_THRESHOLD": settings.bayes_toxic_threshold,
        "BAYES_UPDATE_STRENGTH": settings.bayes_update_strength,
        "QUEUE_FILL_FAST_MS": settings.queue_fill_fast_ms,
        "QUEUE_FILL_SLOW_MS": settings.queue_fill_slow_ms,
        "SELF_CAL_EVERY_N_FILLS": float(settings.self_cal_every_n_fills),
        "SELF_CAL_STEP_SIZE": settings.self_cal_step_size,
        "SELF_CAL_MAX_GAMMA": settings.self_cal_max_gamma,
        "SELF_CAL_MIN_GAMMA": settings.self_cal_min_gamma,
        "ANALYTICS_WINDOW": float(settings.analytics_window),
        "EWMA_VOL_SPAN": float(settings.ewma_vol_span),
    }
    for name, value in numeric_checks.items():
        if value <= 0:
            raise ValueError(f"{name} must be greater than 0.")

    if settings.session_start_utc == settings.session_end_utc:
        raise ValueError("SESSION_START_UTC and SESSION_END_UTC cannot be the same.")

    if settings.order_size_btc > settings.max_position_btc:
        raise ValueError("ORDER_SIZE_BTC cannot exceed MAX_POSITION_BTC.")

    if settings.max_position_btc > 5.0:
        raise ValueError("MAX_POSITION_BTC is too high for this bot profile. Keep it at 5 BTC or lower.")

    if settings.max_trade_notional_usd > 100000:
        raise ValueError("MAX_TRADE_NOTIONAL_USD is too high for this bot profile. Keep it at 100000 USD or lower.")

    if settings.max_daily_loss_usd > 10000:
        raise ValueError("MAX_DAILY_LOSS_USD is too high for this bot profile. Keep it at 10000 USD or lower.")

    if settings.max_consecutive_losses > 100:
        raise ValueError("MAX_CONSECUTIVE_LOSSES is too high for this bot profile. Keep it at 100 or lower.")

    if settings.market_maker_target_spread_bps < 1:
        raise ValueError("MARKET_MAKER_TARGET_SPREAD_BPS must be at least 1 bps.")

    if settings.market_maker_inventory_skew_bps < 0:
        raise ValueError("MARKET_MAKER_INVENTORY_SKEW_BPS cannot be negative.")

    if not 0 <= settings.market_maker_size_skew_factor <= 2:
        raise ValueError("MARKET_MAKER_SIZE_SKEW_FACTOR must be between 0 and 2.")

    if settings.market_maker_reprice_bps <= 0:
        raise ValueError("MARKET_MAKER_REPRICE_BPS must be greater than 0.")

    if settings.min_net_edge_bps < 0:
        raise ValueError("MIN_NET_EDGE_BPS cannot be negative.")

    if settings.spread_min_bps <= 0:
        raise ValueError("SPREAD_MIN_BPS must be greater than 0.")

    if settings.spread_max_bps <= settings.spread_min_bps:
        raise ValueError("SPREAD_MAX_BPS must be greater than SPREAD_MIN_BPS.")

    if settings.as_gamma <= 0:
        raise ValueError("AS_GAMMA must be greater than 0.")

    if settings.as_kappa <= 0:
        raise ValueError("AS_KAPPA must be greater than 0.")

    if settings.ofi_window < 10:
        raise ValueError("OFI_WINDOW must be at least 10.")

    if not 0.5 <= settings.bayes_toxic_threshold <= 1.0:
        raise ValueError("BAYES_TOXIC_THRESHOLD must be between 0.5 and 1.0.")

    if settings.bayes_update_strength <= 0:
        raise ValueError("BAYES_UPDATE_STRENGTH must be greater than 0.")

    if settings.queue_fill_fast_ms <= 0 or settings.queue_fill_slow_ms <= 0:
        raise ValueError("QUEUE_FILL_FAST_MS and QUEUE_FILL_SLOW_MS must be greater than 0.")

    if settings.self_cal_every_n_fills <= 0:
        raise ValueError("SELF_CAL_EVERY_N_FILLS must be greater than 0.")

    if not 0 < settings.self_cal_step_size <= 1:
        raise ValueError("SELF_CAL_STEP_SIZE must be between 0 and 1.")

    if settings.self_cal_max_gamma <= settings.self_cal_min_gamma:
        raise ValueError("SELF_CAL_MAX_GAMMA must be greater than SELF_CAL_MIN_GAMMA.")

    if settings.analytics_window < 50:
        raise ValueError("ANALYTICS_WINDOW must be at least 50.")

    if settings.ewma_vol_span < 5:
        raise ValueError("EWMA_VOL_SPAN must be at least 5.")


def load_settings() -> Settings:
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()

    if not api_key or not secret_key:
        raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required.")

    return Settings(
        alpaca_api_key=api_key,
        alpaca_secret_key=secret_key,
        paper=_get_env_bool("ALPACA_PAPER", True),
        symbol=os.getenv("SYMBOL", "BTC/USD").strip(),
        enable_shorts=_get_env_bool("ENABLE_SHORTS", False),
        dry_run=_get_env_bool("DRY_RUN", False),
        dashboard_enabled=_get_env_bool("DASHBOARD_ENABLED", True),
        dashboard_interval_seconds=_get_env_int("DASHBOARD_INTERVAL_SECONDS", 2),
        session_start_utc=_parse_utc_time(os.getenv("SESSION_START_UTC", "00:00")),
        session_end_utc=_parse_utc_time(os.getenv("SESSION_END_UTC", "23:59")),
        max_trades_per_session=_get_env_int("MAX_TRADES_PER_SESSION", 100),
        order_reprice_seconds=_get_env_float("ORDER_REPRICE_SECONDS", 15.0),
        funding_rate_bps_per_hour=_get_env_float("FUNDING_RATE_BPS_PER_HOUR", 0.0),
        market_maker_target_spread_bps=_get_env_float("MARKET_MAKER_TARGET_SPREAD_BPS", 3.0),
        market_maker_inventory_skew_bps=_get_env_float("MARKET_MAKER_INVENTORY_SKEW_BPS", 3.0),
        market_maker_size_skew_factor=_get_env_float("MARKET_MAKER_SIZE_SKEW_FACTOR", 0.75),
        market_maker_reprice_bps=_get_env_float("MARKET_MAKER_REPRICE_BPS", 1.0),
        min_net_edge_bps=_get_env_float("MIN_NET_EDGE_BPS", 12.0),
        spread_vol_factor=_get_env_float("SPREAD_VOL_FACTOR", 0.4),
        spread_inventory_factor=_get_env_float("SPREAD_INVENTORY_FACTOR", 1.2),
        spread_ofi_factor=_get_env_float("SPREAD_OFI_FACTOR", 0.8),
        spread_min_bps=_get_env_float("SPREAD_MIN_BPS", 1.5),
        spread_max_bps=_get_env_float("SPREAD_MAX_BPS", 25.0),
        as_gamma=_get_env_float("AS_GAMMA", 0.1),
        as_kappa=_get_env_float("AS_KAPPA", 1.5),
        as_momentum_factor=_get_env_float("AS_MOMENTUM_FACTOR", 0.3),
        ofi_window=_get_env_int("OFI_WINDOW", 50),
        ofi_skew_bps=_get_env_float("OFI_SKEW_BPS", 1.5),
        ofi_signal_threshold=_get_env_float("OFI_SIGNAL_THRESHOLD", 0.3),
        bayes_toxic_prior=_get_env_float("BAYES_TOXIC_PRIOR", 0.2),
        bayes_toxic_threshold=_get_env_float("BAYES_TOXIC_THRESHOLD", 0.70),
        bayes_update_strength=_get_env_float("BAYES_UPDATE_STRENGTH", 0.15),
        queue_fill_fast_ms=_get_env_float("QUEUE_FILL_FAST_MS", 800.0),
        queue_fill_slow_ms=_get_env_float("QUEUE_FILL_SLOW_MS", 4000.0),
        self_cal_enabled=_get_env_bool("SELF_CAL_ENABLED", True),
        self_cal_every_n_fills=_get_env_int("SELF_CAL_EVERY_N_FILLS", 500),
        self_cal_step_size=_get_env_float("SELF_CAL_STEP_SIZE", 0.05),
        self_cal_max_gamma=_get_env_float("SELF_CAL_MAX_GAMMA", 0.5),
        self_cal_min_gamma=_get_env_float("SELF_CAL_MIN_GAMMA", 0.02),
        analytics_window=_get_env_int("ANALYTICS_WINDOW", 300),
        ewma_vol_span=_get_env_int("EWMA_VOL_SPAN", 20),
        loop_interval_seconds=_get_env_float("LOOP_INTERVAL_SECONDS", 1.0),
        stale_data_seconds=_get_env_int("STALE_DATA_SECONDS", 10),
        max_position_btc=_get_env_float("MAX_POSITION_BTC", 0.01),
        max_trade_notional_usd=_get_env_float("MAX_TRADE_NOTIONAL_USD", 250.0),
        max_daily_loss_usd=_get_env_float("MAX_DAILY_LOSS_USD", 50.0),
        max_consecutive_losses=_get_env_int("MAX_CONSECUTIVE_LOSSES", 4),
        cooldown_seconds=_get_env_int("COOLDOWN_SECONDS", 30),
        momentum_lookback_ticks=_get_env_int("MOMENTUM_LOOKBACK_TICKS", 5),
        spread_bps_min=_get_env_float("SPREAD_BPS_MIN", 1.5),
        take_profit_bps=_get_env_float("TAKE_PROFIT_BPS", 4.0),
        stop_loss_bps=_get_env_float("STOP_LOSS_BPS", 3.0),
        max_holding_seconds=_get_env_int("MAX_HOLDING_SECONDS", 30),
        order_size_btc=_get_env_float("ORDER_SIZE_BTC", 0.005),
        order_price_offset_bps=_get_env_float("ORDER_PRICE_OFFSET_BPS", 0.5),
        db_path=Path(os.getenv("DB_PATH", "runtime/trades.db")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


def load_and_validate_settings() -> Settings:
    settings = load_settings()
    validate_settings(settings)
    return settings
