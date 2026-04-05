"""
Factory for instantiating exchange adapters.

Usage:
    adapter = AdapterFactory.create('alpaca', settings)
    # or
    adapter = AdapterFactory.create('coinbase', api_key=..., secret=...)
"""

import logging
from typing import Optional

from .base import ExchangeAdapter
from .alpaca import AlpacaAdapter
from .coinbase import CoinbaseAdapter
from .fix import FixAdapter
from ..config import Settings

logger = logging.getLogger(__name__)


class AdapterFactory:
    """Factory for creating exchange adapter instances."""

    @staticmethod
    def create(
        exchange_name: str,
        settings: Optional[Settings] = None,
        **kwargs
    ) -> ExchangeAdapter:
        """
        Create an exchange adapter instance.
        
        Args:
            exchange_name: Name of exchange ('alpaca', 'coinbase', etc.)
            settings: Settings object (for Alpaca)
            **kwargs: Additional arguments (for Coinbase: api_key, secret, passphrase, product_id)
            
        Returns:
            ExchangeAdapter subclass instance
            
        Raises:
            ValueError: If exchange_name is not supported
        """
        exchange_lower = exchange_name.lower().strip()

        if exchange_lower == "alpaca":
            if settings is None:
                raise ValueError("AlpacaAdapter requires Settings object")
            logger.info("Creating AlpacaAdapter")
            return AlpacaAdapter(settings)

        elif exchange_lower == "coinbase":
            product_id = kwargs.get("product_id", "BTC-USD")
            api_key = kwargs.get("api_key", "")
            secret = kwargs.get("secret", "")
            passphrase = kwargs.get("passphrase", "")
            logger.info(f"Creating CoinbaseAdapter for {product_id}")
            return CoinbaseAdapter(
                product_id=product_id,
                api_key=api_key,
                secret=secret,
                passphrase=passphrase
            )

        elif exchange_lower in {"fix", "coinbase_fix", "kraken_fix"}:
            venue = kwargs.get("venue", "coinbase" if "coinbase" in exchange_lower else "kraken")
            symbol = kwargs.get("symbol", "BTCUSD")
            paper_mode = kwargs.get("paper_mode", True)
            logger.info(f"Creating FixAdapter for venue={venue}, symbol={symbol}")
            return FixAdapter(venue=venue, symbol=symbol, paper_mode=paper_mode)

        else:
            raise ValueError(
                f"Unsupported exchange: {exchange_name}. "
                f"Supported: alpaca, coinbase, fix, coinbase_fix, kraken_fix"
            )

    @staticmethod
    def list_supported() -> list[str]:
        """Return list of supported exchange names."""
        return ["alpaca", "coinbase", "fix", "coinbase_fix", "kraken_fix"]
