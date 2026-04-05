from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                price REAL NOT NULL,
                client_order_id TEXT,
                order_id TEXT,
                realized_pnl_usd REAL,
                est_fee_usd REAL,
                est_slippage_usd REAL DEFAULT 0,
                funding_pnl_usd REAL DEFAULT 0
            )
            """
        )
        self.conn.commit()
        self._ensure_columns(
            "fills",
            {
                "est_slippage_usd": "REAL DEFAULT 0",
                "funding_pnl_usd": "REAL DEFAULT 0",
            },
        )

    def _ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, definition in columns.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.conn.commit()

    def log_event(self, ts: str, event_type: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO events(ts, event_type, payload_json) VALUES (?, ?, ?)",
            (ts, event_type, json.dumps(payload, default=str)),
        )
        self.conn.commit()

    def log_fill(
        self,
        ts: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        client_order_id: str | None,
        order_id: str | None,
        realized_pnl_usd: float,
        est_fee_usd: float,
        est_slippage_usd: float,
        funding_pnl_usd: float,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO fills(
                ts, symbol, side, qty, price, client_order_id, order_id, realized_pnl_usd, est_fee_usd, est_slippage_usd, funding_pnl_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, symbol, side, qty, price, client_order_id, order_id, realized_pnl_usd, est_fee_usd, est_slippage_usd, funding_pnl_usd),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
