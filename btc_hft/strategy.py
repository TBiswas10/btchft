from __future__ import annotations

from collections import deque

from .models import QuoteSnapshot, Signal


class MomentumScalper:
    def __init__(self, lookback_ticks: int, spread_bps_min: float) -> None:
        self.lookback_ticks = max(lookback_ticks, 2)
        self.spread_bps_min = spread_bps_min
        self.mids: deque[float] = deque(maxlen=self.lookback_ticks)

    def on_quote(self, quote: QuoteSnapshot) -> Signal:
        if quote.mid <= 0:
            return Signal.HOLD

        self.mids.append(quote.mid)
        if len(self.mids) < self.lookback_ticks:
            return Signal.HOLD

        if quote.spread_bps < self.spread_bps_min:
            return Signal.HOLD

        first = self.mids[0]
        last = self.mids[-1]
        if first <= 0:
            return Signal.HOLD

        move_bps = ((last - first) / first) * 10000
        if move_bps >= 1.0:
            return Signal.BUY
        if move_bps <= -1.0:
            return Signal.SELL
        return Signal.HOLD
