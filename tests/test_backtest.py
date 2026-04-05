from __future__ import annotations

from pathlib import Path

from btc_hft.backtest import compare_strategies, load_replay_ticks
from btc_hft.models import QuoteSnapshot


def _make_ticks() -> list:
    from datetime import datetime, timedelta, timezone
    from btc_hft.backtest import ReplayTick

    base = datetime.now(timezone.utc)
    ticks = []
    price = 50000.0
    for i in range(40):
        price += 8.0 if i % 2 == 0 else -6.0
        bid = price - 1.0
        ask = price + 1.0
        ticks.append(
            ReplayTick(
                ts=base + timedelta(seconds=i),
                bid=bid,
                ask=ask,
                price=price,
                regime="normal",
                volatility_bps=2.0,
                ofi_score=0.4 if i % 3 == 0 else -0.4,
                p_toxic=0.2,
                bayes_regime="noise",
                liquidation_mode=False,
                analytics={},
                raw={},
            )
        )
    return ticks


def test_load_replay_ticks_reconstructs_bid_ask(tmp_path: Path) -> None:
    log_path = tmp_path / "bot.log"
    log_path.write_text(
        '{"ts":"2026-04-05T00:00:00+00:00","level":"INFO","logger":"btc_hft.bot","message":"Heartbeat","event":"heartbeat","symbol":"BTC/USD","price":50000.0,"book_mid":50000.0,"book_spread_bps":4.0,"regime":"normal","volatility_bps":2.0}\n',
        encoding="utf-8",
    )
    ticks = load_replay_ticks(log_path)
    assert len(ticks) == 1
    assert ticks[0].bid > 0
    assert ticks[0].ask > ticks[0].bid


def test_compare_strategies_produces_metrics(settings) -> None:
    ticks = _make_ticks()
    reports = compare_strategies(ticks, settings)
    assert set(reports) == {"baseline", "upgraded"}
    assert reports["baseline"].metrics.total_trades >= 0
    assert reports["upgraded"].metrics.total_trades >= 0
    assert reports["upgraded"].metrics.ofi_validation["positive_sample_count"] >= 0.0
