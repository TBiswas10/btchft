from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import PositionState, RuntimeState


@dataclass(frozen=True)
class FillImpact:
    realized_pnl_usd: float
    fee_usd: float
    slippage_usd: float
    funding_pnl_usd: float


def _signed_qty(side: str, qty: float) -> float:
    return qty if side == "buy" else -qty


def apply_fill_to_state(
    state: RuntimeState,
    side: str,
    qty: float,
    fill_price: float,
    limit_price: float,
    fee_rate: float,
    now: datetime | None = None,
) -> FillImpact:
    now = now or datetime.now(timezone.utc)
    signed_qty = _signed_qty(side, qty)

    realized = 0.0
    prev_qty = state.position.qty_btc
    prev_avg = state.position.avg_entry_price

    if prev_qty == 0:
        state.position.qty_btc = signed_qty
        state.position.avg_entry_price = fill_price
        state.position.entry_time = now
    else:
        new_qty = prev_qty + signed_qty

        if prev_qty > 0 and signed_qty < 0:
            closed_qty = min(abs(signed_qty), abs(prev_qty))
            realized = (fill_price - prev_avg) * closed_qty
        elif prev_qty < 0 and signed_qty > 0:
            closed_qty = min(abs(signed_qty), abs(prev_qty))
            realized = (prev_avg - fill_price) * closed_qty

        if new_qty == 0:
            state.position = PositionState()
        elif (prev_qty > 0 and new_qty > 0) or (prev_qty < 0 and new_qty < 0):
            weighted_notional = (abs(prev_qty) * prev_avg) + (abs(signed_qty) * fill_price)
            state.position.qty_btc = new_qty
            state.position.avg_entry_price = weighted_notional / abs(prev_qty + signed_qty)
            if state.position.entry_time is None:
                state.position.entry_time = now
        else:
            state.position.qty_btc = new_qty
            state.position.avg_entry_price = fill_price
            state.position.entry_time = now

    fee = abs(qty * fill_price) * fee_rate
    if side == "buy":
        slippage = max(fill_price - limit_price, 0.0) * qty
    else:
        slippage = max(limit_price - fill_price, 0.0) * qty

    state.realized_pnl_usd += realized
    state.estimated_fees_usd += fee
    state.estimated_slippage_usd += slippage
    state.daily_realized_pnl_usd += realized
    state.daily_estimated_fees_usd += fee
    state.daily_estimated_slippage_usd += slippage
    state.last_trade_time = now
    state.trade_count += 1
    state.daily_trade_count += 1

    if realized > 0:
        state.wins += 1
        state.consecutive_losses = 0
    elif realized < 0:
        state.losses += 1
        state.consecutive_losses += 1

    return FillImpact(
        realized_pnl_usd=realized,
        fee_usd=fee,
        slippage_usd=slippage,
        funding_pnl_usd=0.0,
    )


def apply_funding_to_state(
    state: RuntimeState,
    mark_price: float,
    funding_rate_bps_per_hour: float,
    now: datetime | None = None,
) -> float:
    now = now or datetime.now(timezone.utc)
    if funding_rate_bps_per_hour == 0 or state.position.qty_btc == 0:
        state.last_funding_applied_at = now
        return 0.0

    last_applied = state.last_funding_applied_at or now
    elapsed_hours = max((now - last_applied).total_seconds() / 3600.0, 0.0)
    if elapsed_hours <= 0:
        return 0.0

    notional = abs(state.position.qty_btc) * mark_price
    position_sign = 1.0 if state.position.qty_btc > 0 else -1.0
    funding = -position_sign * notional * (funding_rate_bps_per_hour / 10000.0) * elapsed_hours
    state.funding_pnl_usd += funding
    state.daily_funding_pnl_usd += funding
    state.last_funding_applied_at = now
    return funding


def mark_to_market_unrealized_pnl(state: RuntimeState, mark_price: float) -> float:
    pos = state.position
    if pos.qty_btc == 0 or pos.avg_entry_price <= 0:
        return 0.0
    if pos.qty_btc > 0:
        return (mark_price - pos.avg_entry_price) * abs(pos.qty_btc)
    return (pos.avg_entry_price - mark_price) * abs(pos.qty_btc)
