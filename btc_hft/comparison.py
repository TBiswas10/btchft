from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TradeRow:
    order_id: str | None
    client_order_id: str | None
    side: str
    qty: float
    price: float
    realized_pnl_usd: float = 0.0
    est_fee_usd: float = 0.0
    est_slippage_usd: float = 0.0
    funding_pnl_usd: float = 0.0


@dataclass(frozen=True)
class ComparisonSummary:
    expected_count: int
    actual_count: int
    matched_count: int
    missing_expected: int
    missing_actual: int
    realized_pnl_delta_usd: float
    fee_delta_usd: float
    slippage_delta_usd: float
    funding_delta_usd: float


def load_expected_rows(path: Path) -> list[TradeRow]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("trades", [])
        return [TradeRow(**row) for row in rows]

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows: list[TradeRow] = []
        for row in reader:
            rows.append(
                TradeRow(
                    order_id=row.get("order_id") or None,
                    client_order_id=row.get("client_order_id") or None,
                    side=row["side"],
                    qty=float(row["qty"]),
                    price=float(row["price"]),
                    realized_pnl_usd=float(row.get("realized_pnl_usd", 0.0) or 0.0),
                    est_fee_usd=float(row.get("est_fee_usd", 0.0) or 0.0),
                    est_slippage_usd=float(row.get("est_slippage_usd", 0.0) or 0.0),
                    funding_pnl_usd=float(row.get("funding_pnl_usd", 0.0) or 0.0),
                )
            )
        return rows


def load_actual_rows(db_path: Path) -> list[TradeRow]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT order_id, client_order_id, side, qty, price, realized_pnl_usd, est_fee_usd, est_slippage_usd, funding_pnl_usd FROM fills ORDER BY id"
        ).fetchall()
        return [TradeRow(*row) for row in rows]
    finally:
        conn.close()


def compare_trade_rows(expected: list[TradeRow], actual: list[TradeRow]) -> ComparisonSummary:
    actual_by_key = {
        row.order_id or row.client_order_id or f"idx-{index}": row
        for index, row in enumerate(actual)
    }

    matched = 0
    realized_delta = 0.0
    fee_delta = 0.0
    slippage_delta = 0.0
    funding_delta = 0.0
    missing_expected = 0

    for index, expected_row in enumerate(expected):
        key = expected_row.order_id or expected_row.client_order_id or f"idx-{index}"
        actual_row = actual_by_key.pop(key, None)
        if actual_row is None:
            missing_expected += 1
            continue
        matched += 1
        realized_delta += actual_row.realized_pnl_usd - expected_row.realized_pnl_usd
        fee_delta += actual_row.est_fee_usd - expected_row.est_fee_usd
        slippage_delta += actual_row.est_slippage_usd - expected_row.est_slippage_usd
        funding_delta += actual_row.funding_pnl_usd - expected_row.funding_pnl_usd

    return ComparisonSummary(
        expected_count=len(expected),
        actual_count=len(actual),
        matched_count=matched,
        missing_expected=missing_expected,
        missing_actual=len(actual_by_key),
        realized_pnl_delta_usd=realized_delta,
        fee_delta_usd=fee_delta,
        slippage_delta_usd=slippage_delta,
        funding_delta_usd=funding_delta,
    )


def summary_to_dict(summary: ComparisonSummary) -> dict[str, Any]:
    return asdict(summary)
