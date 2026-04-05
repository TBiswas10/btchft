from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone

from .config import Settings
from .models import RuntimeState


@dataclass(frozen=True)
class SessionDecision:
    should_stop: bool
    reason: str | None
    reset_daily: bool
    session_day: str


class SessionGuard:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _utc_day(now: datetime) -> str:
        return now.astimezone(timezone.utc).date().isoformat()

    def _within_window(self, now: datetime) -> bool:
        current = now.astimezone(timezone.utc).time()
        start = self.settings.session_start_utc
        end = self.settings.session_end_utc
        if start < end:
            return start <= current <= end
        return current >= start or current <= end

    def evaluate(self, state: RuntimeState, now: datetime) -> SessionDecision:
        day = self._utc_day(now)
        reset_daily = state.trading_day_utc != day
        if reset_daily:
            state.trading_day_utc = day
            state.daily_realized_pnl_usd = 0.0
            state.daily_estimated_fees_usd = 0.0
            state.daily_estimated_slippage_usd = 0.0
            state.daily_funding_pnl_usd = 0.0
            state.daily_trade_count = 0
            state.consecutive_losses = 0
            state.session_started_at = now
            state.last_funding_applied_at = now

        if not self._within_window(now):
            return SessionDecision(True, "outside_session_window", reset_daily, day)

        if state.daily_trade_count >= self.settings.max_trades_per_session:
            return SessionDecision(True, "max_trades_per_session", reset_daily, day)

        return SessionDecision(False, None, reset_daily, day)
