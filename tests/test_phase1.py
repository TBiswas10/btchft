"""
Tests for Phase 1: Multi-exchange market data and order routing.

Validates:
- MultiExchangeMarketDataManager quote aggregation
- MultiExchangeOrderRouter failover logic
- Health status tracking
- Exchange switching logic
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timezone

from btc_hft.adapters import (
    MultiExchangeMarketDataManager,
    MultiExchangeOrderRouter,
    AggregatedQuote,
    Quote,
    OrderRoutingStrategy,
    ExchangeHealthStatus,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_alpaca_adapter():
    """Mock Alpaca adapter."""
    adapter = Mock()
    adapter.exchange_name = "Alpaca"
    adapter.start = AsyncMock()
    adapter.stop = AsyncMock()
    adapter.get_quote = AsyncMock()
    adapter.submit_order = AsyncMock()
    adapter.cancel_order = AsyncMock()
    adapter.get_order_status = AsyncMock()
    return adapter


@pytest.fixture
def mock_coinbase_adapter():
    """Mock Coinbase adapter."""
    adapter = Mock()
    adapter.exchange_name = "Coinbase"
    adapter.start = AsyncMock()
    adapter.stop = AsyncMock()
    adapter.get_quote = AsyncMock()
    adapter.submit_order = AsyncMock()
    adapter.cancel_order = AsyncMock()
    adapter.get_order_status = AsyncMock()
    return adapter


def make_quote(exchange: str, bid: float, ask: float, bid_size: float = 100.0, ask_size: float = 100.0) -> Quote:
    """Helper to create a Quote object."""
    return Quote(
        exchange=exchange,
        symbol="BTCUSD",
        bid_price=bid,
        ask_price=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        timestamp=datetime.now(timezone.utc)
    )


# ============================================================================
# MultiExchangeMarketDataManager Tests
# ============================================================================

class TestMultiExchangeMarketDataManager:
    """Test market data aggregation."""

    @pytest.mark.asyncio
    async def test_manager_initializes_with_primary_and_fallbacks(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Manager can be initialized with primary + fallbacks."""
        manager = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        
        assert manager.primary_adapter == mock_alpaca_adapter
        assert manager.fallback_adapters == [mock_coinbase_adapter]
        assert len(manager.all_adapters) == 2

    @pytest.mark.asyncio
    async def test_manager_starts_all_adapters(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Manager starts all adapters."""
        manager = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        
        await manager.start()
        
        mock_alpaca_adapter.start.assert_called_once()
        mock_coinbase_adapter.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_manager_stops_all_adapters(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Manager stops all adapters."""
        manager = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        
        await manager.stop()
        
        mock_alpaca_adapter.stop.assert_called_once()
        mock_coinbase_adapter.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_manager_aggregates_best_bid_ask(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Manager returns best bid/ask across exchanges."""
        mock_alpaca_adapter.get_quote.return_value = make_quote("Alpaca", bid=40000.0, ask=40010.0, bid_size=1.0, ask_size=1.0)
        mock_coinbase_adapter.get_quote.return_value = make_quote("Coinbase", bid=40005.0, ask=40008.0, bid_size=2.0, ask_size=2.0)
        
        manager = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        agg_quote = await manager.get_aggregated_quote()
        
        assert agg_quote is not None
        assert agg_quote.bid_price == 40005.0  # Best bid from Coinbase
        assert agg_quote.ask_price == 40008.0  # Best ask from Coinbase
        assert agg_quote.bid_exchange == "Coinbase"
        assert agg_quote.ask_exchange == "Coinbase"

    @pytest.mark.asyncio
    async def test_manager_mixed_exchangesbest_bid_ask(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Manager can get best bid from one exchange and best ask from another."""
        mock_alpaca_adapter.get_quote.return_value = make_quote("Alpaca", bid=40010.0, ask=40015.0)
        mock_coinbase_adapter.get_quote.return_value = make_quote("Coinbase", bid=40005.0, ask=40008.0)
        
        manager = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        agg_quote = await manager.get_aggregated_quote()
        
        assert agg_quote.bid_price == 40010.0  # Best bid from Alpaca
        assert agg_quote.ask_price == 40008.0  # Best ask from Coinbase

    @pytest.mark.asyncio
    async def test_manager_returns_none_if_no_quotes(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Manager returns None if no quotes available."""
        mock_alpaca_adapter.get_quote.return_value = None
        mock_coinbase_adapter.get_quote.return_value = None
        
        manager = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        agg_quote = await manager.get_aggregated_quote()
        
        assert agg_quote is None

    @pytest.mark.asyncio
    async def test_aggregated_quote_mid_price(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """AggregatedQuote calculates mid price correctly."""
        mock_alpaca_adapter.get_quote.return_value = make_quote("Alpaca", bid=40000.0, ask=40010.0)
        mock_coinbase_adapter.get_quote.return_value = make_quote("Coinbase", bid=40000.0, ask=40010.0)
        
        manager = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        agg_quote = await manager.get_aggregated_quote()
        
        assert agg_quote.mid_price == 40005.0  # (40000 + 40010) / 2

    @pytest.mark.asyncio
    async def test_aggregated_quote_spread_bps(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """AggregatedQuote calculates spread in basis points."""
        mock_alpaca_adapter.get_quote.return_value = make_quote("Alpaca", bid=40000.0, ask=40010.0)
        mock_coinbase_adapter.get_quote.return_value = make_quote("Coinbase", bid=40000.0, ask=40010.0)
        
        manager = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        agg_quote = await manager.get_aggregated_quote()
        
        # spread = (40010 - 40000) / mid * 10000 = 10 / 40005 * 10000 ≈ 2.5 bps
        assert agg_quote.spread_bps > 2.4  # Allow small rounding error
        assert agg_quote.spread_bps < 2.6

    def test_manager_tracks_health_status(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Manager tracks health status of exchanges."""
        manager = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        health = manager.get_health_status()
        
        assert "Alpaca" in health
        assert "Coinbase" in health
        assert health["Alpaca"] == "disconnected"


# ============================================================================
# MultiExchangeOrderRouter Tests
# ============================================================================

class TestMultiExchangeOrderRouter:
    """Test order routing and failover."""

    @pytest.mark.asyncio
    async def test_router_submits_to_primary_with_fallback_strategy(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Router submits to primary with fallback strategy."""
        mock_alpaca_adapter.submit_order.return_value = "order-123"
        
        router = MultiExchangeOrderRouter(
            mock_alpaca_adapter,
            [mock_coinbase_adapter],
            strategy=OrderRoutingStrategy.FALLBACK
        )
        
        result = await router.submit_order("BTCUSD", "buy", 0.1, 40000.0)
        
        assert result.success
        assert result.order_id == "order-123"
        assert result.exchange_name == "Alpaca"
        assert result.attempt_count == 1

    @pytest.mark.asyncio
    async def test_router_failover_to_secondary(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Router falls back to secondary if primary fails."""
        mock_alpaca_adapter.submit_order.return_value = None  # Primary fails
        mock_coinbase_adapter.submit_order.return_value = "order-456"  # Secondary succeeds
        
        router = MultiExchangeOrderRouter(
            mock_alpaca_adapter,
            [mock_coinbase_adapter],
            strategy=OrderRoutingStrategy.FALLBACK
        )
        
        result = await router.submit_order("BTCUSD", "buy", 0.1, 40000.0)
        
        assert result.success
        assert result.order_id == "order-456"
        assert result.exchange_name == "Coinbase"
        assert result.attempt_count == 2

    @pytest.mark.asyncio
    async def test_router_primary_only_no_fallback(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Router with PRIMARY_ONLY strategy doesn't fallback."""
        mock_alpaca_adapter.submit_order.return_value = None
        
        router = MultiExchangeOrderRouter(
            mock_alpaca_adapter,
            [mock_coinbase_adapter],
            strategy=OrderRoutingStrategy.PRIMARY_ONLY
        )
        
        result = await router.submit_order("BTCUSD", "buy", 0.1, 40000.0)
        
        assert not result.success
        assert result.attempt_count == 1
        mock_coinbase_adapter.submit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_router_tracks_order_exchange(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Router tracks which exchange an order was placed on."""
        mock_alpaca_adapter.submit_order.return_value = "order-123"
        
        router = MultiExchangeOrderRouter(mock_alpaca_adapter, [mock_coinbase_adapter])
        result = await router.submit_order("BTCUSD", "buy", 0.1, 40000.0)
        
        assert router.get_order_exchange("order-123") == "Alpaca"

    @pytest.mark.asyncio
    async def test_router_cancels_order_on_correct_exchange(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Router cancels order on the exchange it was placed on."""
        mock_alpaca_adapter.submit_order.return_value = "order-123"
        mock_alpaca_adapter.cancel_order.return_value = True
        
        router = MultiExchangeOrderRouter(mock_alpaca_adapter, [mock_coinbase_adapter])
        
        # Place order
        await router.submit_order("BTCUSD", "buy", 0.1, 40000.0)
        
        # Cancel order
        result = await router.cancel_order("order-123")
        
        assert result
        mock_alpaca_adapter.cancel_order.assert_called_once_with("order-123")
        mock_coinbase_adapter.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_router_all_exchanges_fail(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Router reports failure when all exchanges fail."""
        mock_alpaca_adapter.submit_order.return_value = None
        mock_coinbase_adapter.submit_order.return_value = None
        
        router = MultiExchangeOrderRouter(
            mock_alpaca_adapter,
            [mock_coinbase_adapter],
            strategy=OrderRoutingStrategy.FALLBACK
        )
        
        result = await router.submit_order("BTCUSD", "buy", 0.1, 40000.0)
        
        assert not result.success
        assert result.attempt_count == 2
        assert "All 2 exchanges failed" in result.reason

    @pytest.mark.asyncio
    async def test_router_exception_handling(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Router handles exceptions from adapters."""
        mock_alpaca_adapter.submit_order.side_effect = Exception("API error")
        mock_coinbase_adapter.submit_order.return_value = "order-456"
        
        router = MultiExchangeOrderRouter(
            mock_alpaca_adapter,
            [mock_coinbase_adapter],
            strategy=OrderRoutingStrategy.FALLBACK
        )
        
        result = await router.submit_order("BTCUSD", "buy", 0.1, 40000.0)
        
        assert result.success
        assert result.exchange_name == "Coinbase"
        assert result.attempt_count == 2


# ============================================================================
# Integration Tests
# ============================================================================

class TestPhase1Integration:
    """Integration tests for multi-exchange."""

    @pytest.mark.asyncio
    async def test_market_data_and_router_work_together(self, mock_alpaca_adapter, mock_coinbase_adapter):
        """Market data and router can work together."""
        # Setup quotes
        mock_alpaca_adapter.get_quote.return_value = make_quote("Alpaca", bid=40000.0, ask=40010.0)
        mock_coinbase_adapter.get_quote.return_value = make_quote("Coinbase", bid=40005.0, ask=40008.0)
        
        # Setup order submission: Alpaca fails, Coinbase succeeds
        mock_alpaca_adapter.submit_order.return_value = None  # Primary fails
        mock_coinbase_adapter.submit_order.return_value = "order-123"  # Secondary succeeds
        
        # Create managers
        market_mgr = MultiExchangeMarketDataManager(mock_alpaca_adapter, [mock_coinbase_adapter])
        router = MultiExchangeOrderRouter(mock_alpaca_adapter, [mock_coinbase_adapter])
        
        # Get aggregated quote
        agg_quote = await market_mgr.get_aggregated_quote()
        assert agg_quote is not None
        assert agg_quote.ask_exchange == "Coinbase"
        
        # Place order with fallback strategy
        result = await router.submit_order("BTCUSD", "buy", 0.1, agg_quote.ask_price)
        assert result.success
        assert result.exchange_name == "Coinbase"  # Should fallback to Coinbase


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
