"""
Abstract base class for exchange adapters.

All exchange-specific implementations (Alpaca, Coinbase, Kraken, etc.) inherit from
ExchangeAdapter and implement the required methods and properties.

This ensures uniform API surfaces across exchanges - the bot can swap adapters by
changing 1 line of config.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class Quote:
    """Market quote from an exchange."""
    exchange: str
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    timestamp: datetime


@dataclass
class Fill:
    """Trade fill from an order."""
    order_id: str
    exchange: str
    symbol: str
    side: str  # "buy" or "sell"
    qty: float
    price: float
    timestamp: datetime


@dataclass
class OrderStatus:
    """Status of a submitted order."""
    order_id: str
    side: str  # "buy" or "sell"
    qty: float
    filled_qty: float
    avg_fill_price: float
    status: str  # "pending", "partially_filled", "filled", "canceled", "rejected"
    reject_reason: Optional[str] = None


class ExchangeAdapter(ABC):
    """
    Abstract base class for exchange adapters.
    
    Each concrete adapter (AlpacaAdapter, CoinbaseAdapter) implements:
    - MarketDataService: Quote streaming via websocket
    - TradingService: Order submission, cancellation, reconciliation
    - AccountService: Position, balance, fees
    """

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        """Return the exchange name (e.g., 'Alpaca', 'Coinbase')."""
        pass

    @property
    @abstractmethod
    def paper_mode(self) -> bool:
        """Return True if in paper trading mode, False if live."""
        pass

    @property
    @abstractmethod
    def min_order_notional(self) -> float:
        """Minimum order notional in USD (e.g., 25.0)."""
        pass

    @property
    @abstractmethod
    def maker_fee_bps(self) -> float:
        """Maker fee in basis points (e.g., 0.5 = 0.005%)."""
        pass

    @property
    @abstractmethod
    def taker_fee_bps(self) -> float:
        """Taker fee in basis points (e.g., 1.0 = 0.01%)."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if websocket/API connection is healthy."""
        pass

    # ============================================================================
    # Lifecycle
    # ============================================================================

    @abstractmethod
    async def start(self) -> None:
        """Connect to exchange (websocket, authenticate, subscribe to quotes)."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect from exchange (close websocket, cleanup)."""
        pass

    # ============================================================================
    # Market Data
    # ============================================================================

    @abstractmethod
    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """
        Get the latest quote for a symbol (from cached websocket data).
        
        Args:
            symbol: Trading pair (e.g., "BTCUSD")
            
        Returns:
            Quote object with bid/ask, or None if stale/unavailable.
        """
        pass

    @abstractmethod
    async def subscribe_quotes(self, symbols: list[str]) -> None:
        """
        Subscribe to live quote updates for symbols.
        
        Args:
            symbols: List of trading pairs (e.g., ["BTCUSD", "ETHUSD"])
        """
        pass

    # ============================================================================
    # Account & Position
    # ============================================================================

    @abstractmethod
    async def get_position(self, symbol: str) -> tuple[float, float]:
        """
        Get current position (qty, entry_price).
        
        Args:
            symbol: Trading pair (e.g., "BTCUSD")
            
        Returns:
            (qty_btc, avg_entry_price) tuple
        """
        pass

    @abstractmethod
    async def get_balance(self) -> float:
        """Get available buying power in USD."""
        pass

    @abstractmethod
    async def validate_paper_balance(self) -> bool:
        """
        Validate that paper account has sufficient funds to start trading.
        
        Returns:
            True if balance >= MAX_TRADE_NOTIONAL_USD, False otherwise.
        """
        pass

    # ============================================================================
    # Order Lifecycle
    # ============================================================================

    @abstractmethod
    async def submit_order(
        self,
        symbol: str,
        side: str,  # "buy" or "sell"
        qty: float,
        price: float,
    ) -> Optional[str]:  # Returns order_id on success, None on rejection/error
        """
        Submit a limit order.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSD")
            side: "buy" or "sell"
            qty: Quantity in base asset (e.g., BTC)
            price: Limit price in quote asset (e.g., USD)
            
        Returns:
            order_id if accepted, None if rejected/error
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order.
        
        Args:
            order_id: Order ID returned by submit_order
            
        Returns:
            True if canceled successfully, False if error/not found
        """
        pass

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Optional[OrderStatus]:
        """
        Get status of a submitted order.
        
        Args:
            order_id: Order ID returned by submit_order
            
        Returns:
            OrderStatus object, or None if not found
        """
        pass

    @abstractmethod
    async def get_fills(self, limit: int = 100) -> list[Fill]:
        """
        Get recent fills (for reconciliation).
        
        Args:
            limit: Maximum number of fills to return
            
        Returns:
            List of Fill objects (most recent first)
        """
        pass

    # ============================================================================
    # Utility
    # ============================================================================

    def log_info(self, message: str) -> None:
        """Log info-level message with exchange prefix."""
        logger.info(f"[{self.exchange_name}] {message}")

    def log_warning(self, message: str) -> None:
        """Log warning-level message with exchange prefix."""
        logger.warning(f"[{self.exchange_name}] {message}")

    def log_error(self, message: str) -> None:
        """Log error-level message with exchange prefix."""
        logger.error(f"[{self.exchange_name}] {message}")
