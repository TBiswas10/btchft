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
        market_maker_target_spread_bps=_get_env_float("MARKET_MAKER_TARGET_SPREAD_BPS", 8.0),
        market_maker_inventory_skew_bps=_get_env_float("MARKET_MAKER_INVENTORY_SKEW_BPS", 3.0),
        market_maker_size_skew_factor=_get_env_float("MARKET_MAKER_SIZE_SKEW_FACTOR", 0.75),
        market_maker_reprice_bps=_get_env_float("MARKET_MAKER_REPRICE_BPS", 1.0),
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
        order_size_btc=_get_env_float("ORDER_SIZE_BTC", 0.001),
        order_price_offset_bps=_get_env_float("ORDER_PRICE_OFFSET_BPS", 0.5),
        db_path=Path(os.getenv("DB_PATH", "runtime/trades.db")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


def load_and_validate_settings() -> Settings:
    settings = load_settings()
    validate_settings(settings)
    return settings
