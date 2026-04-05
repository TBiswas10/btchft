"""
HFT microstructure intelligence layer.

Contains:
  - EWMAVolatility: exponentially weighted moving average volatility
  - MomentumSignal: multi-horizon short-term momentum
  - OrderFlowImbalance: buy vs sell pressure from quote dynamics
  - QueuePositionInference: infers queue depth from fill latency
  - BayesianRegimeDetector: probabilistic toxic vs noise flow classification
  - MicrostructureEngine: combines all of the above
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional


class EWMAVolatility:
    """Exponentially weighted moving average volatility estimator."""

    def __init__(self, span: int = 20) -> None:
        self.span = max(5, span)
        self._alpha = 2.0 / (self.span + 1)
        self._var: float = 0.0
        self._prev_mid: Optional[float] = None
        self._tick_count: int = 0

    def update(self, mid: float) -> float:
        if mid <= 0:
            return math.sqrt(self._var) * 100.0

        if self._prev_mid is None or self._prev_mid <= 0:
            self._prev_mid = mid
            return 0.0

        log_return_bps = math.log(mid / self._prev_mid) * 10000.0
        self._var = self._alpha * (log_return_bps ** 2) + (1 - self._alpha) * self._var
        self._prev_mid = mid
        self._tick_count += 1
        return math.sqrt(self._var) * 100.0

    @property
    def current_bps(self) -> float:
        return math.sqrt(self._var) * 100.0

    @property
    def is_warmed_up(self) -> bool:
        return self._tick_count >= self.span


@dataclass(frozen=True)
class MomentumSnapshot:
    short_bps: float
    medium_bps: float
    long_bps: float
    composite_bps: float


class MomentumSignal:
    def __init__(self) -> None:
        self._mids: deque[float] = deque(maxlen=35)

    def update(self, mid: float) -> MomentumSnapshot:
        if mid > 0:
            self._mids.append(mid)

        if len(self._mids) < 4:
            return MomentumSnapshot(0.0, 0.0, 0.0, 0.0)

        series = list(self._mids)
        current = series[-1]

        def _bps_move(lookback: int) -> float:
            if len(series) < lookback + 1:
                return 0.0
            past = series[-(lookback + 1)]
            if past <= 0:
                return 0.0
            return ((current - past) / past) * 10000.0

        short = _bps_move(3)
        medium = _bps_move(10)
        long_ = _bps_move(30)
        composite = 0.5 * short + 0.3 * medium + 0.2 * long_

        return MomentumSnapshot(
            short_bps=round(short, 4),
            medium_bps=round(medium, 4),
            long_bps=round(long_, 4),
            composite_bps=round(composite, 4),
        )


class OrderFlowImbalance:
    def __init__(self, window: int = 50) -> None:
        self.window = max(10, window)
        self._ticks: deque[float] = deque(maxlen=self.window)
        self._prev_mid: Optional[float] = None

    def update(self, bid: float, ask: float) -> None:
        if bid <= 0 or ask <= 0:
            return
        mid = (bid + ask) / 2.0
        if self._prev_mid is None or self._prev_mid <= 0:
            self._prev_mid = mid
            return
        move_bps = ((mid - self._prev_mid) / self._prev_mid) * 10000.0
        self._ticks.append(move_bps)
        self._prev_mid = mid

    @property
    def score(self) -> float:
        if not self._ticks:
            return 0.0
        pos = sum(x for x in self._ticks if x > 0)
        neg = sum(abs(x) for x in self._ticks if x < 0)
        total = pos + neg
        if total < 1e-12:
            return 0.0
        return (pos - neg) / total

    @property
    def signal_strength(self) -> str:
        s = abs(self.score)
        if s >= 0.6:
            return "strong"
        if s >= 0.3:
            return "moderate"
        return "weak"

    @property
    def is_bullish(self) -> bool:
        return self.score > 0.3

    @property
    def is_bearish(self) -> bool:
        return self.score < -0.3


class QueuePositionInference:
    def __init__(self, fast_threshold_ms: float = 800.0, slow_threshold_ms: float = 4000.0, window: int = 30) -> None:
        self.fast_ms = fast_threshold_ms
        self.slow_ms = slow_threshold_ms
        self._latencies_ms: deque[float] = deque(maxlen=window)
        self._order_submitted_at: Optional[float] = None

    def on_order_submitted(self) -> None:
        self._order_submitted_at = time.perf_counter()

    def on_fill(self) -> None:
        if self._order_submitted_at is None:
            return
        elapsed_ms = (time.perf_counter() - self._order_submitted_at) * 1000.0
        self._latencies_ms.append(elapsed_ms)
        self._order_submitted_at = None

    def on_cancel_or_replace(self) -> None:
        self._order_submitted_at = None

    @property
    def position(self) -> str:
        if len(self._latencies_ms) < 5:
            return "unknown"
        recent = list(self._latencies_ms)[-10:]
        avg_ms = sum(recent) / len(recent)
        if avg_ms <= self.fast_ms:
            return "front"
        if avg_ms >= self.slow_ms:
            return "back"
        return "unknown"

    @property
    def avg_fill_latency_ms(self) -> float:
        if not self._latencies_ms:
            return 0.0
        return sum(self._latencies_ms) / len(self._latencies_ms)

    @property
    def recommended_reprice_multiplier(self) -> float:
        pos = self.position
        if pos == "front":
            return 1.4
        if pos == "back":
            return 0.6
        return 1.0


@dataclass
class BayesianRegimeState:
    p_toxic: float
    p_noise: float
    regime: str
    fill_count: int
    adverse_fill_count: int


class BayesianRegimeDetector:
    P_ADVERSE_GIVEN_TOXIC = 0.75
    P_ADVERSE_GIVEN_NOISE = 0.35

    def __init__(self, prior_toxic: float = 0.2, toxic_threshold: float = 0.70, update_strength: float = 0.15) -> None:
        self.p_toxic = max(0.05, min(0.95, prior_toxic))
        self.p_noise = 1.0 - self.p_toxic
        self.toxic_threshold = toxic_threshold
        self.update_strength = update_strength
        self._fill_count = 0
        self._adverse_count = 0
        self._benign_count = 0

    def update_on_fill(self, side: str, fill_price: float, subsequent_mid: float) -> None:
        if fill_price <= 0 or subsequent_mid <= 0:
            return

        self._fill_count += 1

        if side == "buy":
            move_bps = ((subsequent_mid - fill_price) / fill_price) * 10000.0
            is_adverse = move_bps < -0.5
        else:
            move_bps = ((fill_price - subsequent_mid) / fill_price) * 10000.0
            is_adverse = move_bps < -0.5

        if is_adverse:
            self._adverse_count += 1
            p_evidence_toxic = self.P_ADVERSE_GIVEN_TOXIC
            p_evidence_noise = self.P_ADVERSE_GIVEN_NOISE
        else:
            self._benign_count += 1
            p_evidence_toxic = 1.0 - self.P_ADVERSE_GIVEN_TOXIC
            p_evidence_noise = 1.0 - self.P_ADVERSE_GIVEN_NOISE

        raw_p_toxic = self.p_toxic * p_evidence_toxic
        raw_p_noise = self.p_noise * p_evidence_noise
        normaliser = raw_p_toxic + raw_p_noise + 1e-12

        new_p_toxic = raw_p_toxic / normaliser
        self.p_toxic = (1 - self.update_strength) * self.p_toxic + self.update_strength * new_p_toxic
        self.p_noise = 1.0 - self.p_toxic

    def decay_toward_prior(self, prior_toxic: float = 0.2, decay: float = 0.005) -> None:
        self.p_toxic = (1 - decay) * self.p_toxic + decay * prior_toxic
        self.p_noise = 1.0 - self.p_toxic

    @property
    def state(self) -> BayesianRegimeState:
        if self.p_toxic >= self.toxic_threshold:
            regime = "toxic"
        elif self.p_toxic <= (1 - self.toxic_threshold):
            regime = "noise"
        else:
            regime = "uncertain"

        return BayesianRegimeState(
            p_toxic=round(self.p_toxic, 4),
            p_noise=round(self.p_noise, 4),
            regime=regime,
            fill_count=self._fill_count,
            adverse_fill_count=self._adverse_count,
        )

    @property
    def is_toxic(self) -> bool:
        return self.p_toxic >= self.toxic_threshold

    @property
    def should_liquidate(self) -> bool:
        return self.is_toxic


@dataclass(frozen=True)
class MicrostructureSnapshot:
    ofi_score: float
    ofi_signal_strength: str
    vol_bps: float
    tick_vol_bps: float
    momentum: MomentumSnapshot
    queue_position: str
    queue_avg_latency_ms: float
    queue_reprice_multiplier: float
    bayes_p_toxic: float
    bayes_regime: str
    should_liquidate: bool
    tick_count: int


class MicrostructureEngine:
    def __init__(
        self,
        ofi_window: int = 50,
        vol_span: int = 20,
        prior_toxic: float = 0.2,
        toxic_threshold: float = 0.70,
        update_strength: float = 0.15,
        queue_fast_ms: float = 800.0,
        queue_slow_ms: float = 4000.0,
    ) -> None:
        self.ofi = OrderFlowImbalance(window=ofi_window)
        self.vol = EWMAVolatility(span=vol_span)
        self.momentum = MomentumSignal()
        self.queue = QueuePositionInference(fast_threshold_ms=queue_fast_ms, slow_threshold_ms=queue_slow_ms)
        self.bayes = BayesianRegimeDetector(prior_toxic=prior_toxic, toxic_threshold=toxic_threshold, update_strength=update_strength)
        self._tick_count = 0
        self._prior_toxic = prior_toxic

    def update(self, bid: float, ask: float) -> MicrostructureSnapshot:
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        self.ofi.update(bid, ask)
        vol_bps = self.vol.update(mid)
        mom = self.momentum.update(mid)
        self.bayes.decay_toward_prior(prior_toxic=self._prior_toxic)
        self._tick_count += 1

        return MicrostructureSnapshot(
            ofi_score=self.ofi.score,
            ofi_signal_strength=self.ofi.signal_strength,
            vol_bps=vol_bps,
            tick_vol_bps=self.vol.current_bps,
            momentum=mom,
            queue_position=self.queue.position,
            queue_avg_latency_ms=self.queue.avg_fill_latency_ms,
            queue_reprice_multiplier=self.queue.recommended_reprice_multiplier,
            bayes_p_toxic=self.bayes.p_toxic,
            bayes_regime=self.bayes.state.regime,
            should_liquidate=self.bayes.should_liquidate,
            tick_count=self._tick_count,
        )

    def on_order_submitted(self) -> None:
        self.queue.on_order_submitted()

    def on_fill(self, side: str, fill_price: float, subsequent_mid: float) -> None:
        self.queue.on_fill()
        self.bayes.update_on_fill(side, fill_price, subsequent_mid)

    def on_cancel_or_replace(self) -> None:
        self.queue.on_cancel_or_replace()
