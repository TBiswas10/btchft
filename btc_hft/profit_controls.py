from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class NetEdgeDecision:
    should_trade: bool
    net_edge_bps: float
    reason: str


class NetEdgeGate:
    """Fee/slippage/toxicity-aware edge gate."""

    def __init__(self, min_net_edge_bps: float) -> None:
        self.min_net_edge_bps = max(0.0, min_net_edge_bps)

    def evaluate(
        self,
        expected_edge_bps: float,
        fee_bps: float,
        slippage_bps: float,
        adverse_selection_bps: float,
    ) -> NetEdgeDecision:
        net = expected_edge_bps - fee_bps - slippage_bps - adverse_selection_bps
        if net >= self.min_net_edge_bps:
            return NetEdgeDecision(True, net, "net_edge_ok")
        return NetEdgeDecision(False, net, "net_edge_below_threshold")


@dataclass(frozen=True)
class RegimeSnapshot:
    regime: str
    volatility_bps: float
    trend_bps: float


class RegimeDetector:
    """Simple rolling classifier for trend/volatility regimes."""

    def __init__(self, lookback: int = 40) -> None:
        self.lookback = max(5, lookback)
        self.mids: deque[float] = deque(maxlen=self.lookback)

    def update(self, mid: float) -> RegimeSnapshot:
        if mid <= 0:
            return RegimeSnapshot("unknown", 0.0, 0.0)

        self.mids.append(mid)
        if len(self.mids) < 5:
            return RegimeSnapshot("warmup", 0.0, 0.0)

        series = list(self.mids)
        returns = []
        for i in range(1, len(series)):
            prev = series[i - 1]
            curr = series[i]
            if prev > 0:
                returns.append(((curr - prev) / prev) * 10000.0)

        if not returns:
            return RegimeSnapshot("warmup", 0.0, 0.0)

        mean_abs = sum(abs(x) for x in returns) / len(returns)
        trend = ((series[-1] - series[0]) / max(series[0], 1e-9)) * 10000.0

        if mean_abs >= 3.0:
            regime = "high_vol"
        elif abs(trend) >= 8.0:
            regime = "trend"
        elif mean_abs <= 1.0:
            regime = "quiet"
        else:
            regime = "normal"

        return RegimeSnapshot(regime, mean_abs, trend)


class AdverseSelectionGuard:
    """Pauses quoting briefly after toxic one-sided moves."""

    def __init__(self, move_bps_threshold: float = 6.0, cooldown_seconds: int = 2) -> None:
        self.move_bps_threshold = max(0.0, move_bps_threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self._prev_mid: float | None = None
        self._paused_until: datetime | None = None

    def update_and_check(self, mid: float, now: datetime | None = None) -> tuple[bool, str | None]:
        now = now or datetime.now(timezone.utc)

        if self._paused_until is not None and now < self._paused_until:
            self._prev_mid = mid if mid > 0 else self._prev_mid
            return True, "adverse_selection_cooldown"

        if mid <= 0:
            return False, None

        if self._prev_mid is None:
            self._prev_mid = mid
            return False, None

        move_bps = ((mid - self._prev_mid) / max(self._prev_mid, 1e-9)) * 10000.0
        self._prev_mid = mid

        if abs(move_bps) >= self.move_bps_threshold:
            self._paused_until = now + timedelta(seconds=self.cooldown_seconds)
            return True, "adverse_selection_spike"

        return False, None


@dataclass(frozen=True)
class ExecutionQualityMetrics:
    submitted: int
    filled: int
    canceled_or_replaced: int
    rejected: int
    fill_ratio: float
    reject_ratio: float
    avg_slippage_usd: float


class ExecutionQualityMonitor:
    """Tracks quality KPIs and signals when strategy should de-risk."""

    def __init__(self) -> None:
        self.submitted = 0
        self.filled = 0
        self.canceled_or_replaced = 0
        self.rejected = 0
        self._slippage_samples: deque[float] = deque(maxlen=200)

    def on_submitted(self) -> None:
        self.submitted += 1

    def on_fill(self, slippage_usd: float) -> None:
        self.filled += 1
        self._slippage_samples.append(max(0.0, slippage_usd))

    def on_canceled_or_replaced(self) -> None:
        self.canceled_or_replaced += 1

    def on_rejected(self) -> None:
        self.rejected += 1

    def snapshot(self) -> ExecutionQualityMetrics:
        submitted = max(self.submitted, 1)
        fill_ratio = self.filled / submitted
        reject_ratio = self.rejected / submitted
        avg_slippage = sum(self._slippage_samples) / len(self._slippage_samples) if self._slippage_samples else 0.0
        return ExecutionQualityMetrics(
            submitted=self.submitted,
            filled=self.filled,
            canceled_or_replaced=self.canceled_or_replaced,
            rejected=self.rejected,
            fill_ratio=fill_ratio,
            reject_ratio=reject_ratio,
            avg_slippage_usd=avg_slippage,
        )

    def should_derisk(
        self,
        min_fill_ratio: float,
        max_reject_ratio: float,
        max_avg_slippage_usd: float,
    ) -> tuple[bool, str | None, ExecutionQualityMetrics]:
        metrics = self.snapshot()

        if self.submitted < 10:
            return False, None, metrics

        if metrics.fill_ratio < min_fill_ratio:
            return True, "low_fill_ratio", metrics
        if metrics.reject_ratio > max_reject_ratio:
            return True, "high_reject_ratio", metrics
        if metrics.avg_slippage_usd > max_avg_slippage_usd:
            return True, "high_avg_slippage", metrics

        return False, None, metrics


@dataclass(frozen=True)
class PnLAttribution:
    spread_capture_usd: float
    fees_usd: float
    slippage_usd: float
    funding_usd: float
    realized_usd: float


def build_pnl_attribution(
    realized_usd: float,
    fees_usd: float,
    slippage_usd: float,
    funding_usd: float,
) -> PnLAttribution:
    spread_capture = realized_usd + fees_usd + slippage_usd - funding_usd
    return PnLAttribution(
        spread_capture_usd=spread_capture,
        fees_usd=fees_usd,
        slippage_usd=slippage_usd,
        funding_usd=funding_usd,
        realized_usd=realized_usd,
    )
