from __future__ import annotations

from btc_hft.comparison import TradeRow, compare_trade_rows


def test_compare_trade_rows_reports_deltas():
    expected = [
        TradeRow(order_id="1", client_order_id=None, side="buy", qty=1.0, price=100.0, realized_pnl_usd=2.0, est_fee_usd=0.1),
    ]
    actual = [
        TradeRow(order_id="1", client_order_id=None, side="buy", qty=1.0, price=101.0, realized_pnl_usd=3.0, est_fee_usd=0.2, est_slippage_usd=0.05, funding_pnl_usd=0.01),
    ]

    summary = compare_trade_rows(expected, actual)
    assert summary.matched_count == 1
    assert summary.realized_pnl_delta_usd == 1.0
    assert summary.fee_delta_usd == 0.1
    assert summary.slippage_delta_usd == 0.05
    assert summary.funding_delta_usd == 0.01
