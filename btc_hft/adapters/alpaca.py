"""
Alpaca exchange adapter implementing the ExchangeAdapter interface.

This adapter wraps the existing Alpaca API clients (MarketDataService, TradingService)
and implements the polymorphic interface defined in base.py.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

from alpaca.data.live.crypto import CryptoDataStream
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderStatus, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from ..config import Settings
from ..models import QuoteSnapshot
from .base import ExchangeAdapter, Quote, Fill, OrderStatus as AdapterOrderStatus

logger = logging.getLogger(__name__)


@dataclass
class ManagedOrder:
    """Internal order representation."""
    order_id: str
    client_order_id: str
    side: str
    qty: float
    limit_price: float
    submitted_at: datetime
    filled_qty: float = 0.0
    last_status: str = "new"


class AlpacaCryptoDataService:
    """Market data service for Alpaca crypto websocket."""

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
        snapshot = QuoteSnapshot(
            bid=float(quote.bid_price),
            ask=float(quote.ask_price),
            timestamp=ts
        )
        with self._lock:
            self.last_quote = snapshot
            self.last_message_at = ts
            self.last_error = None

    def request_restart(self, reason: str) -> None:
        logger.warning(
            f"[Alpaca] Restarting market stream: {reason}",
            extra={"event": "stream_restart_requested", "reason": reason}
        )
        self.last_error = reason
        self._restart_event.set()
        with self._lock:
            if self._current_stream is not None:
                try:
                    self._current_stream.stop()
                except Exception:
                    logger.exception(
                        "[Alpaca] Failed to stop active market stream for restart",
                        extra={"event": "stream_stop_error"}
                    )

    def is_stale(self, stale_seconds: int) -> bool:
        with self._lock:
            if self.last_message_at is None:
                return True
            age_seconds = (datetime.now(timezone.utc) - self.last_message_at).total_seconds()
            return age_seconds > stale_seconds

    def get_snapshot(self) -> QuoteSnapshot:
        with self._lock:
            return self.last_quote

    def health_snapshot(self) -> dict:
        with self._lock:
            age_seconds = None
            if self.last_message_at is not None:
                age_seconds = (datetime.now(timezone.utc) - self.last_message_at).total_seconds()

            return {
                "connected": bool(self.is_running),
                "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
                "data_age_seconds": age_seconds,
                "last_error": self.last_error,
                "connection_attempts": self.connection_attempts,
            }

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
                        f"[Alpaca] Connecting market data stream for {self.settings.symbol}",
                        extra={"event": "stream_connecting", "symbol": self.settings.symbol}
                    )
                    stream.run()
                    backoff_seconds = 1.0
                except Exception as exc:
                    self.last_error = str(exc)
                    logger.exception(
                        "[Alpaca] Market data stream crashed",
                        extra={"event": "stream_error", "error": str(exc)}
                    )
                finally:
                    try:
                        stream.stop()
                    except Exception:
                        logger.exception("[Alpaca] Failed to stop market stream")
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

    def stop(self) -> None:
        self._stop_event.set()
        try:
            with self._lock:
                if self._current_stream is not None:
                    self._current_stream.stop()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
        except Exception:
            logger.exception("[Alpaca] Failed to stop market stream")
        finally:
            self.is_running = False


class AlpacaTradingService:
    """Trading service for Alpaca order submission."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TradingClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            paper=settings.paper
        )

    def get_account(self):
        return self.client.get_account()

    def validate_paper_balance(self, max_trade_notional_usd: float) -> bool:
        """Validate paper account has sufficient buying power for configured sizing."""
        if not self.settings.paper:
            return True

        account = self.get_account()
        available = float(getattr(account, "buying_power", None) or getattr(account, "cash", None) or 0.0)
        required = float(max_trade_notional_usd) * 5.0
        if available < required:
            raise ValueError(
                f"Paper balance insufficient. Available=${available:.2f}, required=${required:.2f}"
            )
        return True

    def submit_limit_order(
        self,
        side: str,
        qty_btc: float,
        limit_price: float,
        client_order_id: str
    ):
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

        This is safer than relying only on local position state when placing sells.
        """
        try:
            positions = self.client.get_all_positions()
        except Exception:
            logger.exception("[Alpaca] Failed to fetch positions for available BTC")
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


class AlpacaAdapter(ExchangeAdapter):
    """
    Alpaca crypto exchange adapter.
    
    Implements ExchangeAdapter interface for paper/live trading on Alpaca.
    Wraps existing MarketDataService and TradingService.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.market_data = AlpacaCryptoDataService(settings)
        self.trading = AlpacaTradingService(settings)
        self._order_counter = 0
        self._order_lock = threading.Lock()

    @property
    def exchange_name(self) -> str:
        return "Alpaca"

    @property
    def paper_mode(self) -> bool:
        return self.settings.paper

    @property
    def min_order_notional(self) -> float:
        """Alpaca Crypto minimum order is $1."""
        return 1.0

    @property
    def maker_fee_bps(self) -> float:
        """Alpaca Crypto maker fee: 0.1% = 10 bps."""
        return 10.0

    @property
    def taker_fee_bps(self) -> float:
        """Alpaca Crypto taker fee: 0.1% = 10 bps."""
        return 10.0

    @property
    def is_connected(self) -> bool:
        """Return True if websocket is connected and data is fresh."""
        return (
            self.market_data.is_running
            and not self.market_data.is_stale(stale_seconds=5)
        )

    # ============================================================================
    # Lifecycle
    # ============================================================================

    async def start(self) -> None:
        """Start websocket connection and validate account."""
        self.log_info("Starting Alpaca adapter")
        self.market_data.start()
        await self.validate_paper_balance()

    async def stop(self) -> None:
        """Stop websocket connection."""
        self.log_info("Stopping Alpaca adapter")
        self.market_data.stop()

    # ============================================================================
    # Market Data
    # ============================================================================

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """
        Get latest quote from cached websocket data.
        
        Returns None if stale (>5s) or uninitialized.
        """
        if self.market_data.is_stale(stale_seconds=5):
            return None

        snapshot = self.market_data.get_snapshot()
        if snapshot.bid <= 0 or snapshot.ask <= 0:
            return None

        return Quote(
            exchange="Alpaca",
            symbol=symbol,
            bid_price=snapshot.bid,
            ask_price=snapshot.ask,
            bid_size=100.0,  # Placeholder (Alpaca crypto websocket doesn't provide size)
            ask_size=100.0,
            timestamp=snapshot.timestamp
        )

    async def subscribe_quotes(self, symbols: list[str]) -> None:
        """
        Subscribe to quotes for symbols.
        
        For Alpaca, this is already done in start().
        If symbols change, this would restart the stream.
        """
        pass

    # ============================================================================
    # Account & Position
    # ============================================================================

    async def get_position(self, symbol: str) -> tuple[float, float]:
        """
        Get current position (qty_btc, avg_entry_price).
        
        Returns (0, 0) if no position.
        """
        try:
            account = self.trading.get_account()
            # Alpaca returns positions as a list
            for position in getattr(account, "positions", []):
                if position.symbol == self.settings.trading_symbol:
                    qty = float(position.qty)
                    entry_price = float(position.avg_entry_price) if hasattr(position, "avg_entry_price") else 0.0
                    return (qty, entry_price)
            return (0.0, 0.0)
        except Exception as e:
            self.log_error(f"Failed to get position: {e}")
            return (0.0, 0.0)

    async def get_balance(self) -> float:
        """Get available buying power in USD."""
        try:
            account = self.trading.get_account()
            buying_power = float(getattr(account, "buying_power", None) or 0.0)
            return buying_power
        except Exception as e:
            self.log_error(f"Failed to get balance: {e}")
            return 0.0

    async def validate_paper_balance(self) -> bool:
        """
        Validate paper account has minimum balance.
        
        Raises ValueError if balance too low.
        """
        if not self.settings.paper:
            self.log_info("Running in live mode (no balance validation)")
            return True

        try:
            account = self.trading.get_account()
            available = float(
                getattr(account, "buying_power", None)
                or getattr(account, "cash", None)
                or 0.0
            )
            required = self.settings.max_trade_notional_usd * 5  # Require 5x max order notional
            if available < required:
                raise ValueError(
                    f"Paper balance insufficient. Available=${available:.2f}, required=${required:.2f}"
                )
            self.log_info(f"Paper balance OK: ${available:.2f}")
            return True
        except Exception as e:
            self.log_error(f"Balance validation failed: {e}")
            raise

    # ============================================================================
    # Order Lifecycle
    # ============================================================================

    async def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> Optional[str]:
        """
        Submit limit order to Alpaca.
        
        Returns order_id if accepted, None if rejected.
        """
        try:
            with self._order_lock:
                self._order_counter += 1
                client_order_id = f"bot-{self._order_counter}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"

            order = self.trading.submit_limit_order(side, qty, price, client_order_id)
            order_id = order.id
            self.log_info(f"Order submitted: {order_id} ({side} {qty:.4f}@${price:.2f})")
            return order_id

        except Exception as e:
            self.log_warning(f"Order submission rejected: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order."""
        try:
            self.trading.cancel_order(order_id)
            self.log_info(f"Order canceled: {order_id}")
            return True
        except Exception as e:
            self.log_warning(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_order_status(self, order_id: str) -> Optional[AdapterOrderStatus]:
        """Get status of order."""
        try:
            order = self.trading.get_order(order_id)
            status_str = str(order.status).lower()
            
            # Map Alpaca status to adapter status
            if status_str in {"filled"}:
                adapter_status = "filled"
            elif status_str in {"partially_filled"}:
                adapter_status = "partially_filled"
            elif status_str in {"pending_new", "accepted", "new"}:
                adapter_status = "pending"
            elif status_str in {"canceled", "cancelled", "expired"}:
                adapter_status = "canceled"
            elif status_str in {"rejected"}:
                adapter_status = "rejected"
            else:
                adapter_status = "pending"

            return AdapterOrderStatus(
                order_id=order.id,
                side=str(order.side).lower().replace("orderside.", ""),
                qty=float(order.qty),
                filled_qty=float(order.filled_qty),
                avg_fill_price=float(order.filled_avg_price) if order.filled_avg_price else 0.0,
                status=adapter_status,
                reject_reason=getattr(order, "cancel_reason", None)
            )
        except Exception as e:
            self.log_warning(f"Failed to get order status {order_id}: {e}")
            return None

    async def get_fills(self, limit: int = 100) -> list[Fill]:
        """Get recent fills for reconciliation."""
        try:
            # TODO: Implement via Alpaca Activities API
            # For now, return empty list (order_manager.py handles reconciliation)
            return []
        except Exception as e:
            self.log_error(f"Failed to get fills: {e}")
            return []
