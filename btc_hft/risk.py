from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Settings
from .models import RuntimeState


class RiskEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cooldown_until: datetime | None = None

    def is_blocked(self, state: RuntimeState, now: datetime, data_age_seconds: float) -> tuple[bool, str | None]:
        if data_age_seconds > self.settings.stale_data_seconds:
            return True, "stale_market_data"

        if self.cooldown_until and now < self.cooldown_until:
            return True, "cooldown"

        daily_net_pnl = (
            state.daily_realized_pnl_usd
            - state.daily_estimated_fees_usd
            - state.daily_estimated_slippage_usd
            + state.daily_funding_pnl_usd
        )
        if daily_net_pnl <= -abs(self.settings.max_daily_loss_usd):
            return True, "max_daily_loss"

        if state.consecutive_losses >= self.settings.max_consecutive_losses:
            return True, "max_consecutive_losses"

        if abs(state.position.qty_btc) > self.settings.max_position_btc:
            return True, "position_limit"

        return False, None

    def check_new_order(self, state: RuntimeState, qty_btc: float, limit_price: float) -> tuple[bool, str | None]:
        next_qty = state.position.qty_btc + qty_btc
        if abs(next_qty) > self.settings.max_position_btc:
            return False, "would_exceed_position"

        notional = abs(qty_btc * limit_price)
        if notional > self.settings.max_trade_notional_usd:
            return False, "trade_notional_limit"

        return True, None

    def trigger_cooldown(self) -> None:
        self.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=self.settings.cooldown_seconds)
