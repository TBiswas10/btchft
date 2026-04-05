"""FIX protocol adapter skeleton for low-latency institutional venues."""

from datetime import datetime, timezone
from typing import Optional

from .base import ExchangeAdapter, Fill, OrderStatus, Quote
from ..latency.order_book import LocalOrderBookEngine


class FixAdapter(ExchangeAdapter):
    """Phase 3 FIX adapter (simulation/skeleton, transport-ready)."""

    def __init__(self, venue: str, symbol: str = "BTCUSD", paper_mode: bool = True):
        self._venue = venue
        self._symbol = symbol
        self._paper_mode = paper_mode
        self._connected = False
        self._book = LocalOrderBookEngine(symbol=symbol)
        self._orders: dict[str, OrderStatus] = {}
        self._fills: list[Fill] = []
        self._counter = 1

    @property
    def exchange_name(self) -> str:
        return f"{self._venue}-FIX"

    @property
    def paper_mode(self) -> bool:
        return self._paper_mode

    @property
    def min_order_notional(self) -> float:
        return 10.0

    @property
    def maker_fee_bps(self) -> float:
        return 0.8

    @property
    def taker_fee_bps(self) -> float:
        return 1.2

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        self._connected = True

    async def stop(self) -> None:
        self._connected = False

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        if symbol != self._symbol:
            return None

        bid = self._book.best_bid()
        ask = self._book.best_ask()
        if bid is None or ask is None:
            return None

        return Quote(
            exchange=self.exchange_name,
            symbol=symbol,
            bid_price=bid.price,
            ask_price=ask.price,
            bid_size=bid.size,
            ask_size=ask.size,
            timestamp=datetime.now(timezone.utc),
        )

    async def subscribe_quotes(self, symbols: list[str]) -> None:
        # Phase 3 skeleton keeps a local synthetic book to unblock integration tests.
        if self._symbol in symbols:
            self._book.apply_snapshot(bids=[(40000.0, 1.0)], asks=[(40001.0, 1.0)])

    async def get_position(self, symbol: str) -> tuple[float, float]:
        return 0.0, 0.0

    async def get_balance(self) -> float:
        return 100000.0

    async def validate_paper_balance(self) -> bool:
        return True

    async def submit_order(self, symbol: str, side: str, qty: float, price: float) -> Optional[str]:
        if not self._connected or symbol != self._symbol or qty <= 0.0 or price <= 0.0:
            return None

        order_id = f"fix-{self._venue.lower()}-{self._counter}"
        self._counter += 1

        self._orders[order_id] = OrderStatus(
            order_id=order_id,
            side=side,
            qty=qty,
            filled_qty=0.0,
            avg_fill_price=0.0,
            status="pending",
        )
        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        status = self._orders.get(order_id)
        if status is None:
            return False

        self._orders[order_id] = OrderStatus(
            order_id=status.order_id,
            side=status.side,
            qty=status.qty,
            filled_qty=status.filled_qty,
            avg_fill_price=status.avg_fill_price,
            status="canceled",
        )
        return True

    async def get_order_status(self, order_id: str) -> Optional[OrderStatus]:
        return self._orders.get(order_id)

    async def get_fills(self, limit: int = 100) -> list[Fill]:
        return self._fills[-limit:]
