"""
Exchange adapters module.

Provides polymorphic exchange API abstraction via adapter pattern.
All adapters implement the ExchangeAdapter interface defined in base.py.

Multi-exchange support via:
- MultiExchangeMarketDataManager: Aggregate quotes from multiple exchanges
- MultiExchangeOrderRouter: Route orders with failover logic

Usage:
    from btc_hft.adapters import AdapterFactory, MultiExchangeMarketDataManager
    
    adapter = AdapterFactory.create('alpaca', settings=config)
    await adapter.start()
    
    quote = await adapter.get_quote('BTCUSD')
    order_id = await adapter.submit_order('BTCUSD', 'buy', 0.1, 42000.0)
    status = await adapter.get_order_status(order_id)
    
    await adapter.stop()

Multi-exchange example:
    alpaca = AdapterFactory.create('alpaca', settings=config)
    coinbase = AdapterFactory.create('coinbase', ...)
    
    market_mgr = MultiExchangeMarketDataManager(alpaca, [coinbase])
    await market_mgr.start()
    agg_quote = await market_mgr.get_aggregated_quote()
    
    router = MultiExchangeOrderRouter(alpaca, [coinbase])
    routed = await router.submit_order('BTCUSD', 'buy', 0.1, 42000.0)
"""

from .base import ExchangeAdapter, Quote, Fill, OrderStatus
from .alpaca import AlpacaAdapter
from .coinbase import CoinbaseAdapter
from .fix import FixAdapter
from .factory import AdapterFactory
from .multi_exchange import MultiExchangeMarketDataManager, AggregatedQuote, ExchangeHealthStatus
from .order_router import MultiExchangeOrderRouter, OrderRoutingStrategy, RoutedOrder

__all__ = [
    # Base classes
    "ExchangeAdapter",
    "Quote",
    "Fill",
    "OrderStatus",
    # Adapters
    "AlpacaAdapter",
    "CoinbaseAdapter",
    "FixAdapter",
    # Factory
    "AdapterFactory",
    # Multi-exchange
    "MultiExchangeMarketDataManager",
    "AggregatedQuote",
    "ExchangeHealthStatus",
    # Order routing
    "MultiExchangeOrderRouter",
    "OrderRoutingStrategy",
    "RoutedOrder",
]
