"""Low-latency local order book engine for top-of-book and depth calculations."""

from dataclasses import dataclass
from time import perf_counter_ns
from typing import Iterable


@dataclass(frozen=True)
class BookLevel:
    """Single price level in an order book."""

    price: float
    size: float


@dataclass(frozen=True)
class BookSnapshot:
    """Immutable snapshot view for downstream consumers."""

    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    timestamp_ns: int


class LocalOrderBookEngine:
    """In-memory book optimized for frequent read/write operations."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}
        self._timestamp_ns = perf_counter_ns()

    @property
    def timestamp_ns(self) -> int:
        return self._timestamp_ns

    def apply_snapshot(self, bids: Iterable[tuple[float, float]], asks: Iterable[tuple[float, float]]) -> None:
        """Replace full book from exchange snapshot payloads."""
        self._bids = {float(price): float(size) for price, size in bids if float(size) > 0.0}
        self._asks = {float(price): float(size) for price, size in asks if float(size) > 0.0}
        self._timestamp_ns = perf_counter_ns()

    def apply_delta(self, side: str, price: float, size: float) -> None:
        """Apply one level update. size=0 removes the level."""
        levels = self._bids if side.lower() == "bid" else self._asks
        p = float(price)
        s = float(size)
        if s <= 0.0:
            levels.pop(p, None)
        else:
            levels[p] = s
        self._timestamp_ns = perf_counter_ns()

    def best_bid(self) -> BookLevel | None:
        if not self._bids:
            return None
        px = max(self._bids)
        return BookLevel(price=px, size=self._bids[px])

    def best_ask(self) -> BookLevel | None:
        if not self._asks:
            return None
        px = min(self._asks)
        return BookLevel(price=px, size=self._asks[px])

    def mid_price(self) -> float | None:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return (bid.price + ask.price) / 2.0

    def spread_bps(self) -> float | None:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        mid = (bid.price + ask.price) / 2.0
        if mid <= 0:
            return None
        return ((ask.price - bid.price) / mid) * 10000.0

    def depth_notional(self, side: str, levels: int = 5) -> float:
        """Return cumulative notional available for first N levels on a side."""
        if levels <= 0:
            return 0.0

        if side.lower() == "bid":
            sorted_levels = sorted(self._bids.items(), key=lambda x: x[0], reverse=True)
        else:
            sorted_levels = sorted(self._asks.items(), key=lambda x: x[0])

        total = 0.0
        for price, size in sorted_levels[:levels]:
            total += price * size
        return total

    def snapshot(self, levels: int = 10) -> BookSnapshot:
        bid_levels = tuple(
            BookLevel(price=price, size=size)
            for price, size in sorted(self._bids.items(), key=lambda x: x[0], reverse=True)[:levels]
        )
        ask_levels = tuple(
            BookLevel(price=price, size=size)
            for price, size in sorted(self._asks.items(), key=lambda x: x[0])[:levels]
        )
        return BookSnapshot(bids=bid_levels, asks=ask_levels, timestamp_ns=self._timestamp_ns)
