from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from alpaca.data.live.crypto import CryptoDataStream
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderStatus, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from .config import Settings
from .models import QuoteSnapshot

logger = logging.getLogger(__name__)


@dataclass
class ManagedOrder:
    order_id: str
    client_order_id: str
    side: str
    qty: float
    limit_price: float
    submitted_at: datetime
    filled_qty: float = 0.0
    last_status: str = "new"


class MarketDataService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.last_quote: QuoteSnapshot = QuoteSnapshot()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._restart_event = threading.Event()
        self._lock = threading.Lock()
        self._current_stream: Optional[CryptoDataStream] = None
        self.last_message_at: datetime | None = None
        self.last_error: str | None = None
        self.connection_attempts = 0
        self.is_running = False

    def _build_stream(self) -> CryptoDataStream:
        return CryptoDataStream(self.settings.alpaca_api_key, self.settings.alpaca_secret_key)

    async def _quote_handler(self, quote) -> None:
        ts = quote.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        snapshot = QuoteSnapshot(bid=float(quote.bid_price), ask=float(quote.ask_price), timestamp=ts)
        with self._lock:
            self.last_quote = snapshot
            self.last_message_at = ts
            self.last_error = None

    def request_restart(self, reason: str) -> None:
        logger.warning("Restarting market stream", extra={"event": "stream_restart_requested", "reason": reason})
        self.last_error = reason
        self._restart_event.set()
        with self._lock:
            if self._current_stream is not None:
                try:
                    self._current_stream.stop()
                except Exception:
                    logger.exception("Failed to stop active market stream for restart", extra={"event": "stream_stop_error"})

    def health_snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
                "last_error": self.last_error,
                "connection_attempts": self.connection_attempts,
                "is_running": self.is_running,
            }

    def is_stale(self, stale_seconds: int) -> bool:
        with self._lock:
            if self.last_message_at is None:
                return True
            age_seconds = (datetime.now(timezone.utc) - self.last_message_at).total_seconds()
            return age_seconds > stale_seconds

    def start(self) -> None:
        def _run() -> None:
            backoff_seconds = 1.0
            self.is_running = True
            while not self._stop_event.is_set():
                self.connection_attempts += 1
                stream = self._build_stream()
                with self._lock:
                    self._current_stream = stream
                stream.subscribe_quotes(self._quote_handler, self.settings.symbol)
                try:
                    logger.info(
                        "Connecting market data stream",
                        extra={"event": "stream_connecting", "symbol": self.settings.symbol, "attempt": self.connection_attempts},
                    )
                    stream.run()
                    backoff_seconds = 1.0
                except Exception as exc:
                    self.last_error = str(exc)
                    logger.exception("Market data stream crashed", extra={"event": "stream_error"})
                finally:
                    try:
                        stream.stop()
                    except Exception:
                        logger.exception("Failed to stop market stream", extra={"event": "stream_stop_error"})
                    with self._lock:
                        if self._current_stream is stream:
                            self._current_stream = None

                if self._stop_event.is_set():
                    break

                if self._restart_event.is_set():
                    self._restart_event.clear()

                time.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 30.0)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        logger.info("Market data stream started", extra={"event": "stream_started", "symbol": self.settings.symbol})

    def stop(self) -> None:
        self._stop_event.set()
        try:
            with self._lock:
                if self._current_stream is not None:
                    self._current_stream.stop()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
        except Exception:
            logger.exception("Failed to stop market stream", extra={"event": "stream_stop_error"})
        finally:
            self.is_running = False


class TradingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TradingClient(settings.alpaca_api_key, settings.alpaca_secret_key, paper=settings.paper)

    def validate_paper_balance(self, minimum_required_usd: float) -> None:
        if not self.settings.paper:
            return

        account = self.client.get_account()
        available = float(getattr(account, "buying_power", None) or getattr(account, "cash", None) or 0.0)
        if available < minimum_required_usd:
            raise ValueError(
                f"Paper account balance is too low for this bot. Available={available:.2f} USD, required={minimum_required_usd:.2f} USD."
            )

    def submit_limit_order(self, side: str, qty_btc: float, limit_price: float, client_order_id: str):
        req = LimitOrderRequest(
            symbol=self.settings.trading_symbol,
            qty=qty_btc,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            limit_price=round(limit_price, 2),
            client_order_id=client_order_id,
        )
        return self.client.submit_order(req)

    def get_order(self, order_id: str):
        return self.client.get_order_by_id(order_id)

    def cancel_order(self, order_id: str) -> None:
        self.client.cancel_order_by_id(order_id)

    def get_available_btc(self, trading_symbol: str) -> float:
        """
        Return broker-available BTC for the configured symbol.

        Alpaca may report a lower available quantity than local position state
        when balances are reserved or partially reconciled.
        """
        try:
            positions = self.client.get_all_positions()
        except Exception:
            logger.exception("Failed to fetch positions for available BTC")
            return 0.0

        symbol = str(trading_symbol).upper()
        asset = symbol.replace("USD", "")

        for position in positions:
            pos_symbol = str(getattr(position, "symbol", "")).upper()
            if pos_symbol not in {symbol, asset, f"{asset}/USD"}:
                continue
            qty_available = getattr(position, "qty_available", None)
            qty = getattr(position, "qty", None)
            raw = qty_available if qty_available is not None else qty
            try:
                return max(float(raw or 0.0), 0.0)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    @staticmethod
    def is_final_status(status: str) -> bool:
        normalized = str(status).lower()
        return normalized in {"filled", "canceled", "cancelled", "expired", "rejected"}
