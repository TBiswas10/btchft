"""
Multi-exchange order router with intelligent failover.

Routes orders to the best exchange based on:
1. Liquidity (best bid/ask)
2. Reliability (exchange health)
3. Fallback chain (retry on failure)
"""

import logging
from dataclasses import dataclass
from typing import Optional
from enum import Enum

from .base import ExchangeAdapter

logger = logging.getLogger(__name__)


class OrderRoutingStrategy(Enum):
    """Strategy for routing orders across exchanges."""
    BEST_PRICE = "best_price"  # Route to exchange with best price
    PRIMARY_ONLY = "primary_only"  # Only use primary, fail fast
    FALLBACK = "fallback"  # Try primary, then fallbacks in order


@dataclass
class RoutedOrder:
    """Result of routing an order."""
    order_id: Optional[str]  # Order ID if successfully placed
    exchange_name: str       # Which exchange the order was placed on
    attempt_count: int       # Number of exchanges tried
    success: bool            # Whether order was successfully placed
    reason: Optional[str]    # Reason for failure (if any)


class MultiExchangeOrderRouter:
    """
    Routes orders across multiple exchanges with failover logic.
    
    Example usage:
        router = MultiExchangeOrderRouter(
            primary_adapter=alpaca_adapter,
            fallback_adapters=[coinbase_adapter],
            strategy=OrderRoutingStrategy.FALLBACK
        )
        
        routed = await router.submit_order("BTCUSD", "buy", 0.1, 42000.0)
        if routed.success:
            print(f"Order placed on {routed.exchange_name}: {routed.order_id}")
        else:
            print(f"Order failed after {routed.attempt_count} attempts: {routed.reason}")
    """

    def __init__(
        self,
        primary_adapter: ExchangeAdapter,
        fallback_adapters: Optional[list[ExchangeAdapter]] = None,
        strategy: OrderRoutingStrategy = OrderRoutingStrategy.FALLBACK,
    ):
        """
        Initialize multi-exchange order router.
        
        Args:
            primary_adapter: Primary exchange for orders
            fallback_adapters: List of fallback exchanges in priority order
            strategy: Routing strategy (best_price, primary_only, fallback)
        """
        self.primary_adapter = primary_adapter
        self.fallback_adapters = fallback_adapters or []
        self.strategy = strategy
        self.all_adapters = [primary_adapter] + self.fallback_adapters
        self._order_exchange_map: dict[str, str] = {}  # order_id -> exchange_name

        logger.info(
            f"MultiExchangeOrderRouter initialized",
            extra={
                "event": "multi_exchange_router_init",
                "primary": primary_adapter.exchange_name,
                "fallbacks": [a.exchange_name for a in self.fallback_adapters],
                "strategy": strategy.value,
            }
        )

    async def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> RoutedOrder:
        """
        Submit an order with routing strategy.
        
        Args:
            symbol: Trading pair
            side: "buy" or "sell"
            qty: Order quantity
            price: Order price
            
        Returns:
            RoutedOrder with result and routing details
        """
        if self.strategy == OrderRoutingStrategy.PRIMARY_ONLY:
            return await self._submit_to_primary_only(symbol, side, qty, price)
        elif self.strategy == OrderRoutingStrategy.BEST_PRICE:
            return await self._submit_to_best_price(symbol, side, qty, price)
        else:  # FALLBACK
            return await self._submit_with_fallback(symbol, side, qty, price)

    async def _submit_to_primary_only(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> RoutedOrder:
        """Submit to primary only, no fallback."""
        try:
            order_id = await self.primary_adapter.submit_order(symbol, side, qty, price)
            if order_id:
                self._order_exchange_map[order_id] = self.primary_adapter.exchange_name
                logger.info(
                    f"Order placed on {self.primary_adapter.exchange_name}",
                    extra={
                        "event": "order_submitted",
                        "exchange": self.primary_adapter.exchange_name,
                        "order_id": order_id,
                        "side": side,
                        "qty": qty,
                        "price": price,
                    }
                )
                return RoutedOrder(
                    order_id=order_id,
                    exchange_name=self.primary_adapter.exchange_name,
                    attempt_count=1,
                    success=True,
                    reason=None,
                )
            else:
                return RoutedOrder(
                    order_id=None,
                    exchange_name=self.primary_adapter.exchange_name,
                    attempt_count=1,
                    success=False,
                    reason="rejected",
                )
        except Exception as e:
            reason = str(e)
            logger.error(f"Order submission to {self.primary_adapter.exchange_name} failed: {reason}")
            return RoutedOrder(
                order_id=None,
                exchange_name=self.primary_adapter.exchange_name,
                attempt_count=1,
                success=False,
                reason=reason,
            )

    async def _submit_to_best_price(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> RoutedOrder:
        """Submit to exchange with best price (not yet implemented)."""
        # TODO: Implement price comparison logic
        # For now, fall back to primary_only
        return await self._submit_to_primary_only(symbol, side, qty, price)

    async def _submit_with_fallback(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> RoutedOrder:
        """
        Submit order with fallback chain.
        
        Try primary first, then fallbacks in order until success.
        """
        attempts = 0
        last_reason = None

        for adapter in self.all_adapters:
            attempts += 1
            try:
                order_id = await adapter.submit_order(symbol, side, qty, price)
                if order_id:
                    self._order_exchange_map[order_id] = adapter.exchange_name
                    logger.info(
                        f"Order placed on {adapter.exchange_name}",
                        extra={
                            "event": "order_submitted",
                            "exchange": adapter.exchange_name,
                            "order_id": order_id,
                            "side": side,
                            "qty": qty,
                            "price": price,
                        }
                    )
                    return RoutedOrder(
                        order_id=order_id,
                        exchange_name=adapter.exchange_name,
                        attempt_count=attempts,
                        success=True,
                        reason=None,
                    )
                else:
                    last_reason = "rejected"
                    logger.warning(
                        f"Order rejected by {adapter.exchange_name}, trying next exchange"
                    )
                    continue
            except Exception as e:
                last_reason = str(e)
                logger.warning(
                    f"Order submission to {adapter.exchange_name} failed: {last_reason}, trying next exchange"
                )
                continue

        logger.error(
            f"Order submission failed on all {attempts} exchanges",
            extra={
                "event": "order_failed_all_exchanges",
                "attempts": attempts,
                "last_reason": last_reason,
            }
        )
        return RoutedOrder(
            order_id=None,
            exchange_name="none",
            attempt_count=attempts,
            success=False,
            reason=f"All {attempts} exchanges failed. Last reason: {last_reason}",
        )

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order using the exchange it was placed on.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if successfully canceled, False otherwise
        """
        exchange_name = self._order_exchange_map.get(order_id)
        if not exchange_name:
            logger.warning(f"Order {order_id} not found in routing map")
            return False

        adapter = next((a for a in self.all_adapters if a.exchange_name == exchange_name), None)
        if not adapter:
            logger.error(f"Adapter for {exchange_name} not found")
            return False

        try:
            result = await adapter.cancel_order(order_id)
            if result:
                del self._order_exchange_map[order_id]
            return result
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_order_status(self, order_id: str):
        """
        Get status of an order using the exchange it was placed on.
        
        Args:
            order_id: Order ID to check
            
        Returns:
            OrderStatus if found, None otherwise
        """
        exchange_name = self._order_exchange_map.get(order_id)
        if not exchange_name:
            logger.warning(f"Order {order_id} not found in routing map")
            return None

        adapter = next((a for a in self.all_adapters if a.exchange_name == exchange_name), None)
        if not adapter:
            logger.error(f"Adapter for {exchange_name} not found")
            return None

        try:
            return await adapter.get_order_status(order_id)
        except Exception as e:
            logger.error(f"Failed to get order status {order_id}: {e}")
            return None

    def get_order_exchange(self, order_id: str) -> Optional[str]:
        """Get which exchange an order was placed on."""
        return self._order_exchange_map.get(order_id)

    def get_routing_map(self) -> dict[str, str]:
        """Get all order->exchange mappings (for debugging)."""
        return self._order_exchange_map.copy()
