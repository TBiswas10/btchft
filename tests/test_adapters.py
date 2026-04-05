"""
Tests for the exchange adapter pattern.

Validates:
- ExchangeAdapter ABC interface
- AlpacaAdapter implementation
- CoinbaseAdapter implementation (placeholder)
- AdapterFactory functionality
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from btc_hft.adapters import (
    ExchangeAdapter,
    AlpacaAdapter,
    CoinbaseAdapter,
    AdapterFactory,
    Quote,
)
from btc_hft.config import Settings


# ============================================================================
# Note: settings fixture is provided by conftest.py
# ============================================================================


# ============================================================================
# ExchangeAdapter ABC Tests
# ============================================================================

def test_exchange_adapter_is_abstract():
    """ExchangeAdapter cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ExchangeAdapter()


# ============================================================================
# AlpacaAdapter Tests
# ============================================================================

class TestAlpacaAdapter:
    """Test AlpacaAdapter implementation."""

    def test_alpaca_adapter_implements_interface(self, settings):
        """AlpacaAdapter implements ExchangeAdapter interface."""
        adapter = AlpacaAdapter(settings)
        assert isinstance(adapter, ExchangeAdapter)

    def test_alpaca_adapter_properties(self, settings):
        """AlpacaAdapter returns correct properties."""
        adapter = AlpacaAdapter(settings)
        
        assert adapter.exchange_name == "Alpaca"
        assert adapter.paper_mode == True
        assert adapter.min_order_notional == 1.0
        assert adapter.maker_fee_bps == 10.0
        assert adapter.taker_fee_bps == 10.0
        # is_connected depends on market_data thread state
        assert isinstance(adapter.is_connected, bool)

    def test_alpaca_adapter_has_market_data_service(self, settings):
        """AlpacaAdapter wraps market_data service."""
        adapter = AlpacaAdapter(settings)
        assert hasattr(adapter, "market_data")
        assert adapter.market_data is not None

    def test_alpaca_adapter_has_trading_service(self, settings):
        """AlpacaAdapter wraps trading service."""
        adapter = AlpacaAdapter(settings)
        assert hasattr(adapter, "trading")
        assert adapter.trading is not None


# ============================================================================
# CoinbaseAdapter Tests
# ============================================================================

class TestCoinbaseAdapter:
    """Test CoinbaseAdapter implementation (Phase 1 placeholder)."""

    def test_coinbase_adapter_implements_interface(self):
        """CoinbaseAdapter implements ExchangeAdapter interface."""
        adapter = CoinbaseAdapter()
        assert isinstance(adapter, ExchangeAdapter)

    def test_coinbase_adapter_properties(self):
        """CoinbaseAdapter returns correct properties."""
        adapter = CoinbaseAdapter()
        
        assert adapter.exchange_name == "Coinbase"
        assert adapter.paper_mode == True  # Placeholder
        assert adapter.min_order_notional == 10.0
        assert adapter.maker_fee_bps == 40.0
        assert adapter.taker_fee_bps == 60.0

    def test_coinbase_adapter_initialization(self):
        """CoinbaseAdapter can be initialized with credentials."""
        adapter = CoinbaseAdapter(
            product_id="BTC-USDC",
            api_key="key123",
            secret="secret456",
            passphrase="pass789"
        )
        assert adapter.product_id == "BTC-USDC"
        assert adapter.api_key == "key123"
        assert adapter.secret == "secret456"
        assert adapter.passphrase == "pass789"

    @pytest.mark.asyncio
    async def test_coinbase_adapter_methods_exist(self):
        """CoinbaseAdapter has all required abstract methods."""
        adapter = CoinbaseAdapter()
        
        # These should exist without raising AttributeError
        assert hasattr(adapter, 'start')
        assert hasattr(adapter, 'stop')
        assert hasattr(adapter, 'get_quote')
        assert hasattr(adapter, 'submit_order')
        assert hasattr(adapter, 'cancel_order')
        assert hasattr(adapter, 'get_order_status')


# ============================================================================
# AdapterFactory Tests
# ============================================================================

class TestAdapterFactory:
    """Test AdapterFactory functionality."""

    def test_factory_creates_alpaca_adapter(self, settings):
        """Factory creates AlpacaAdapter."""
        adapter = AdapterFactory.create("alpaca", settings=settings)
        assert isinstance(adapter, AlpacaAdapter)

    def test_factory_creates_alpaca_adapter_case_insensitive(self, settings):
        """Factory is case-insensitive."""
        adapter = AdapterFactory.create("ALPACA", settings=settings)
        assert isinstance(adapter, AlpacaAdapter)
        
        adapter = AdapterFactory.create("AlPaCa", settings=settings)
        assert isinstance(adapter, AlpacaAdapter)

    def test_factory_creates_coinbase_adapter(self):
        """Factory creates CoinbaseAdapter."""
        adapter = AdapterFactory.create("coinbase")
        assert isinstance(adapter, CoinbaseAdapter)

    def test_factory_creates_coinbase_with_kwargs(self):
        """Factory passes kwargs to CoinbaseAdapter."""
        adapter = AdapterFactory.create(
            "coinbase",
            product_id="BTC-USDC",
            api_key="key123"
        )
        assert isinstance(adapter, CoinbaseAdapter)
        assert adapter.product_id == "BTC-USDC"
        assert adapter.api_key == "key123"

    def test_factory_raises_on_unsupported_exchange(self, settings):
        """Factory raises ValueError for unsupported exchange."""
        with pytest.raises(ValueError, match="Unsupported exchange"):
            AdapterFactory.create("kraken", settings=settings)

    def test_factory_requires_settings_for_alpaca(self):
        """Factory requires Settings for AlpacaAdapter."""
        with pytest.raises(ValueError, match="requires Settings"):
            AdapterFactory.create("alpaca")  # No settings

    def test_factory_list_supported(self):
        """Factory can list supported exchanges."""
        supported = AdapterFactory.list_supported()
        assert "alpaca" in supported
        assert "coinbase" in supported
        assert len(supported) >= 2


# ============================================================================
# Integration Tests
# ============================================================================

class TestAdapterIntegration:
    """Integration tests for adapter pattern."""

    def test_alpaca_adapter_backward_compatible(self, settings):
        """AlpacaAdapter is backward-compatible with old interface."""
        adapter = AlpacaAdapter(settings)
        # Old code accessed self.market.last_quote and self.trading directly
        assert hasattr(adapter, "market_data")
        assert hasattr(adapter, "trading")
        
    def test_multiple_adapters_coexist(self, settings):
        """Can create multiple adapter instances."""
        alpaca1 = AdapterFactory.create("alpaca", settings=settings)
        alpaca2 = AdapterFactory.create("alpaca", settings=settings)
        coinbase = AdapterFactory.create("coinbase")
        
        assert alpaca1 is not alpaca2  # Different instances
        assert isinstance(alpaca1, AlpacaAdapter)
        assert isinstance(coinbase, CoinbaseAdapter)

    def test_adapter_exchange_name_matches_factory_input(self, settings):
        """Adapter's exchange_name matches factory input (roughly)."""
        for exchange in ["alpaca", "coinbase"]:
            adapter = AdapterFactory.create(exchange, settings=settings if exchange == "alpaca" else {})
            assert exchange.lower() in adapter.exchange_name.lower()


# ============================================================================
# Interface Compliance Tests
# ============================================================================

class TestAdapterInterfaceCompliance:
    """Verify adapters implement all abstract methods."""

    def test_alpaca_adapter_has_all_methods(self, settings):
        """AlpacaAdapter implements all abstract methods."""
        adapter = AlpacaAdapter(settings)
        
        # All abstract methods
        methods = [
            'start', 'stop',
            'get_quote', 'subscribe_quotes',
            'get_position', 'get_balance', 'validate_paper_balance',
            'submit_order', 'cancel_order', 'get_order_status', 'get_fills',
        ]
        
        for method in methods:
            assert hasattr(adapter, method), f"Missing method: {method}"
            assert callable(getattr(adapter, method)), f"Not callable: {method}"

    def test_coinbase_adapter_has_all_methods(self):
        """CoinbaseAdapter implements all abstract methods."""
        adapter = CoinbaseAdapter()
        
        methods = [
            'start', 'stop',
            'get_quote', 'subscribe_quotes',
            'get_position', 'get_balance', 'validate_paper_balance',
            'submit_order', 'cancel_order', 'get_order_status', 'get_fills',
        ]
        
        for method in methods:
            assert hasattr(adapter, method), f"Missing method: {method}"
            assert callable(getattr(adapter, method)), f"Not callable: {method}"

    def test_adapters_have_all_properties(self, settings):
        """Adapters implement all required properties."""
        properties = [
            'exchange_name',
            'paper_mode',
            'min_order_notional',
            'maker_fee_bps',
            'taker_fee_bps',
            'is_connected',
        ]
        
        for adapter in [AlpacaAdapter(settings), CoinbaseAdapter()]:
            for prop in properties:
                assert hasattr(adapter, prop), f"{adapter.exchange_name} missing property: {prop}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
