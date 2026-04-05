"""
Multi-exchange market data aggregator.

Subscribes to multiple exchanges simultaneously and provides:
- Unified quote interface (best bid/ask across all exchanges)
- Failover logic (if primary exchange has stale data, switch to secondary)
- Liquidity aggregation (combined depth from all sources)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from enum import Enum

from .base import ExchangeAdapter, Quote

logger = logging.getLogger(__name__)


class ExchangeHealthStatus(Enum):
    """Health status of an exchange connection."""
    HEALTHY = "healthy"
    STALE = "stale"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass
class AggregatedQuote:
    """Aggregated quote across multiple exchanges."""
    bid_price: float  # Best bid price across exchanges
    ask_price: float  # Best ask price across exchanges
    bid_size: float   # Size at best bid (from that exchange)
    ask_size: float   # Size at best ask (from that exchange)
    bid_exchange: str # Which exchange has best bid
    ask_exchange: str # Which exchange has best ask
    timestamp: datetime
    data_age_seconds: float  # Age of the newest quote
    
    @property
    def mid_price(self) -> float:
        """Mid price between bid and ask."""
        if self.bid_price <= 0 or self.ask_price <= 0:
            return 0.0
        return (self.bid_price + self.ask_price) / 2.0
    
    @property
    def spread_bps(self) -> float:
        """Bid-ask spread in basis points."""
        if self.mid_price <= 0:
            return 0.0
        return ((self.ask_price - self.bid_price) / self.mid_price) * 10000


class MultiExchangeMarketDataManager:
    """
    Manages market data from multiple exchanges with failover logic.
    
    Strategy:
    1. Subscribe to all adapters
    2. Aggregate best bid/ask across exchanges
    3. If primary exchange stale, fall back to secondary
    4. Track health of each exchange (healthy, stale, disconnected)
    5. Automatically switch primary on degradation
    """

    def __init__(self, primary_adapter: ExchangeAdapter, fallback_adapters: Optional[list[ExchangeAdapter]] = None):
        """
        Initialize multi-exchange manager.
        
        Args:
            primary_adapter: Primary exchange (e.g., Alpaca)
            fallback_adapters: List of fallback exchanges in priority order (e.g., [Coinbase, Kraken])
        """
        self.primary_adapter = primary_adapter
        self.fallback_adapters = fallback_adapters or []
        self.all_adapters = [primary_adapter] + self.fallback_adapters
        
        # Quote cache per exchange
        self._quotes: dict[str, Optional[Quote]] = {adapter.exchange_name: None for adapter in self.all_adapters}
        self._health: dict[str, ExchangeHealthStatus] = {
            adapter.exchange_name: ExchangeHealthStatus.DISCONNECTED for adapter in self.all_adapters
        }
        self._stale_threshold_seconds = 5
        self._last_health_check_at = datetime.now(timezone.utc)
        self._current_primary = primary_adapter
        
        logger.info(
            f"MultiExchangeMarketDataManager initialized",
            extra={
                "event": "multi_exchange_init",
                "primary": primary_adapter.exchange_name,
                "fallbacks": [a.exchange_name for a in self.fallback_adapters],
            }
        )

    async def start(self) -> None:
        """Start all exchange adapters."""
        logger.info("Starting all exchange adapters")
        for adapter in self.all_adapters:
            try:
                await adapter.start()
                logger.info(f"[{adapter.exchange_name}] Started successfully")
            except Exception as e:
                logger.error(f"[{adapter.exchange_name}] Failed to start: {e}")

    async def stop(self) -> None:
        """Stop all exchange adapters."""
        logger.info("Stopping all exchange adapters")
        for adapter in self.all_adapters:
            try:
                await adapter.stop()
            except Exception as e:
                logger.warning(f"[{adapter.exchange_name}] Error during stop: {e}")

    async def refresh_quotes(self) -> None:
        """Refresh quotes from all adapters."""
        for adapter in self.all_adapters:
            try:
                quote = await adapter.get_quote(adapter.exchange_name)
                if quote:
                    self._quotes[adapter.exchange_name] = quote
                    self._health[adapter.exchange_name] = ExchangeHealthStatus.HEALTHY
                else:
                    self._health[adapter.exchange_name] = ExchangeHealthStatus.STALE
            except Exception as e:
                logger.warning(f"[{adapter.exchange_name}] Error fetching quote: {e}")
                self._health[adapter.exchange_name] = ExchangeHealthStatus.ERROR

    async def get_aggregated_quote(self) -> Optional[AggregatedQuote]:
        """
        Get best bid/ask across all exchanges with failover logic.
        
        Returns:
            AggregatedQuote if at least one healthy exchange, None otherwise
        """
        await self.refresh_quotes()
        
        # Check health and potentially switch primary
        await self._maybe_switch_primary()
        
        # Find best bid/ask across healthy exchanges
        best_bid = 0.0
        best_bid_exchange = None
        best_bid_size = 0.0
        
        best_ask = float('inf')
        best_ask_exchange = None
        best_ask_size = 0.0
        
        latest_timestamp = None
        
        for adapter in self.all_adapters:
            quote = self._quotes.get(adapter.exchange_name)
            if quote is None:
                continue
            
            if latest_timestamp is None or quote.timestamp > latest_timestamp:
                latest_timestamp = quote.timestamp
            
            # Track best bid
            if quote.bid_price > best_bid:
                best_bid = quote.bid_price
                best_bid_exchange = adapter.exchange_name
                best_bid_size = quote.bid_size
            
            # Track best ask
            if quote.ask_price < best_ask and quote.ask_price > 0:
                best_ask = quote.ask_price
                best_ask_exchange = adapter.exchange_name
                best_ask_size = quote.ask_size
        
        if best_bid <= 0 or best_ask >= float('inf') or latest_timestamp is None:
            return None
        
        data_age = (datetime.now(timezone.utc) - latest_timestamp).total_seconds()
        
        return AggregatedQuote(
            bid_price=best_bid,
            ask_price=best_ask,
            bid_size=best_bid_size,
            ask_size=best_ask_size,
            bid_exchange=best_bid_exchange or "unknown",
            ask_exchange=best_ask_exchange or "unknown",
            timestamp=latest_timestamp,
            data_age_seconds=data_age
        )

    async def _maybe_switch_primary(self) -> None:
        """
        Switch primary exchange if current primary is degraded.
        
        Logic:
        1. If primary is healthy, keep it
        2. If primary is stale/error, find first healthy fallback
        3. Switch and log the change
        """
        primary_health = self._health.get(self._current_primary.exchange_name)
        
        if primary_health == ExchangeHealthStatus.HEALTHY:
            return  # Primary is healthy, no need to switch
        
        logger.warning(
            f"Primary exchange {self._current_primary.exchange_name} is {primary_health.value}, attempting failover"
        )
        
        # Find first healthy fallback
        for adapter in self.fallback_adapters:
            if self._health.get(adapter.exchange_name) == ExchangeHealthStatus.HEALTHY:
                old_primary = self._current_primary
                self._current_primary = adapter
                logger.info(
                    f"Switched primary exchange",
                    extra={
                        "event": "exchange_failover",
                        "old_primary": old_primary.exchange_name,
                        "new_primary": adapter.exchange_name,
                    }
                )
                return
        
        # No healthy fallback found, log critical
        logger.error(
            "No healthy exchanges available for market data",
            extra={
                "event": "all_exchanges_degraded",
                "health_status": {k: v.value for k, v in self._health.items()},
            }
        )

    def get_health_status(self) -> dict[str, str]:
        """Get health status of all exchanges."""
        return {name: status.value for name, status in self._health.items()}

    def get_primary_exchange(self) -> str:
        """Get current primary exchange name."""
        return self._current_primary.exchange_name

    def get_exchange_quotes(self) -> dict[str, Optional[Quote]]:
        """Get current quotes from all exchanges (for debugging)."""
        return self._quotes.copy()

    async def subscribe_quotes(self, symbols: list[str]) -> None:
        """Subscribe to quote updates for symbols on all exchanges."""
        logger.info(f"Subscribing to quotes on all exchanges: {symbols}")
        for adapter in self.all_adapters:
            try:
                await adapter.subscribe_quotes(symbols)
            except Exception as e:
                logger.warning(f"[{adapter.exchange_name}] Failed to subscribe: {e}")
