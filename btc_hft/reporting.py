from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from .models import RuntimeState


def write_end_of_day_report(
    report_dir: Path,
    state: RuntimeState,
    symbol: str,
    stream_health: dict | None = None,
    analytics_snapshot: dict | None = None,
    calibration_state: dict | None = None,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    win_rate = (state.wins / state.trade_count) * 100 if state.trade_count else 0.0
    payload = {
        "generated_at": now.isoformat(),
        "symbol": symbol,
        "trade_count": state.trade_count,
        "daily_trade_count": state.daily_trade_count,
        "wins": state.wins,
        "losses": state.losses,
        "win_rate_pct": round(win_rate, 2),
        "realized_pnl_usd": round(state.realized_pnl_usd, 6),
        "daily_realized_pnl_usd": round(state.daily_realized_pnl_usd, 6),
        "estimated_fees_usd": round(state.estimated_fees_usd, 6),
        "daily_estimated_fees_usd": round(state.daily_estimated_fees_usd, 6),
        "estimated_slippage_usd": round(state.estimated_slippage_usd, 6),
        "daily_estimated_slippage_usd": round(state.daily_estimated_slippage_usd, 6),
        "funding_pnl_usd": round(state.funding_pnl_usd, 6),
        "daily_funding_pnl_usd": round(state.daily_funding_pnl_usd, 6),
        "net_pnl_usd": round(
            state.realized_pnl_usd - state.estimated_fees_usd - state.estimated_slippage_usd + state.funding_pnl_usd,
            6,
        ),
        "final_position": asdict(state.position),
        "blocked_reason": state.blocked_reason,
        "stream_health": stream_health,
        "analytics": analytics_snapshot or {},
        "calibration": calibration_state or {},
    }

    target = report_dir / f"eod_report_{now.strftime('%Y%m%d_%H%M%S')}.json"
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return target
