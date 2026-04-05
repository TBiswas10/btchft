from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Iterable, Sequence

from .analytics import PerformanceAnalytics
from .config import Settings
from .latency import LocalOrderBookEngine
from .microstructure import MicrostructureEngine
from .models import PositionState, QuoteSnapshot, RuntimeState
from .order_manager import FillResult, OrderManager
from .portfolio import apply_fill_to_state, mark_to_market_unrealized_pnl
from .profit_controls import AdverseSelectionGuard, ExecutionQualityMonitor, RegimeDetector
from .decision_policy import DecisionInput, ExpectancyDecisionPolicy, TradeDecision
from .self_calibration import SelfCalibrator
from .spread_surface import SpreadSurface


@dataclass(frozen=True)
class ReplayTick:
    ts: datetime
    bid: float
    ask: float
    price: float
    regime: str = "unknown"
    volatility_bps: float = 0.0
    ofi_score: float = 0.0
    p_toxic: float = 0.0
    bayes_regime: str = "unknown"
    liquidation_mode: bool = False
    analytics: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if self.bid > 0 and self.ask > 0 else self.price


@dataclass(frozen=True)
class BacktestTrade:
    ts: str
    side: str
    qty: float
    limit_price: float
    fill_price: float
    realized_pnl_usd: float
    fee_usd: float
    slippage_usd: float
    regime: str
    ofi_score: float
    queue_position: str
    p_toxic: float
    expected_net_bps: float = 0.0
    realized_net_bps: float = 0.0
    confidence: float = 0.0
    threshold_used: float = 0.0


@dataclass(frozen=True)
class BacktestMetrics:
    total_pnl_usd: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    fill_rate: float
    avg_spread_capture_bps: float
    total_trades: int
    total_fills: int
    edge_blocks: int
    toxicity_vetoes: int
    equity_curve: list[float]
    regime_pnl: dict[str, dict]
    queue_pnl: dict[str, dict]
    ofi_validation: dict[str, float]
    toxicity_validation: dict[str, float]
    spread_validation: dict[str, float]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BacktestReport:
    generated_at: str
    source: str
    strategy_name: str
    parameters: dict
    metrics: BacktestMetrics
    trades: list[BacktestTrade]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["metrics"]["equity_curve"] = self.metrics.equity_curve
        return payload


@dataclass(frozen=True)
class StrategyParams:
    name: str
    upgraded: bool
    as_gamma: float
    spread_vol_factor: float
    spread_inventory_factor: float
    ofi_skew_bps: float
    min_net_edge_bps: float


class LegacyMarketMaker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_plan(self, quote: QuoteSnapshot, position: PositionState) -> dict | None:
        if quote.mid <= 0:
            return None
        inventory_ratio = 0.0
        if self.settings.max_position_btc > 0:
            inventory_ratio = max(min(position.qty_btc / self.settings.max_position_btc, 1.0), -1.0)

        target_spread = max(self.settings.market_maker_target_spread_bps, self.settings.spread_bps_min)
        skew_bps = inventory_ratio * self.settings.market_maker_inventory_skew_bps
        half_spread = max(target_spread / 2.0, 0.5)
        bid_bps = max(half_spread + skew_bps, 0.25)
        ask_bps = max(half_spread - skew_bps, 0.25)

        bid_price = quote.mid * (1 - bid_bps / 10000.0)
        ask_price = quote.mid * (1 + ask_bps / 10000.0)
        base_size = self.settings.order_size_btc * self.settings.market_maker_size_skew_factor
        return {
            "bid_price": bid_price,
            "ask_price": ask_price,
            "bid_qty": base_size,
            "ask_qty": base_size,
            "half_spread_bps": half_spread,
            "inventory_ratio": inventory_ratio,
            "quote_mid": quote.mid,
            "surface_inputs": {},
        }


class BacktestEngine:
    def __init__(self, settings: Settings, strategy: StrategyParams, seed: int = 7) -> None:
        self.settings = settings
        self.strategy = strategy
        self.random = random.Random(seed)
        self.state = RuntimeState(position=PositionState())
        self.regime_detector = RegimeDetector(lookback=settings.momentum_lookback_ticks * 8)
        self.local_book = LocalOrderBookEngine(symbol=settings.trading_symbol)
        self.microstructure = MicrostructureEngine(
            ofi_window=settings.ofi_window,
            vol_span=settings.ewma_vol_span,
            prior_toxic=settings.bayes_toxic_prior,
            toxic_threshold=settings.bayes_toxic_threshold,
            update_strength=settings.bayes_update_strength,
            queue_fast_ms=settings.queue_fill_fast_ms,
            queue_slow_ms=settings.queue_fill_slow_ms,
        )
        self.analytics = PerformanceAnalytics(window=settings.analytics_window)
        self.calibrator = SelfCalibrator(
            initial_gamma=strategy.as_gamma,
            initial_ofi_skew_bps=strategy.ofi_skew_bps,
            initial_min_edge_bps=strategy.min_net_edge_bps,
            step_size=settings.self_cal_step_size,
            max_gamma=settings.self_cal_max_gamma,
            min_gamma=settings.self_cal_min_gamma,
        )
        self.bid_orders = OrderManager(trading=None, dry_run=True)
        self.ask_orders = OrderManager(trading=None, dry_run=True)
        self.decision_policy = ExpectancyDecisionPolicy(base_threshold_bps=max(0.05, strategy.min_net_edge_bps))
        self.exec_quality = ExecutionQualityMonitor()
        self.adverse_guard = AdverseSelectionGuard(move_bps_threshold=settings.take_profit_bps, cooldown_seconds=settings.cooldown_seconds)
        self.market_maker = (
            self._upgrade_market_maker() if strategy.upgraded else LegacyMarketMaker(settings)
        )
        self._equity_curve: list[float] = []
        self._trades: list[BacktestTrade] = []
        self._edge_blocks = 0
        self._toxicity_vetoes = 0
        self._liq_mode = False
        self._halting_reason: str | None = None
        self._pending_quote_snapshots: dict[str, datetime] = {}
        self._ofi_future_moves: list[tuple[float, float]] = []
        self._toxicity_events: list[tuple[float, float]] = []
        self._spread_buckets: dict[str, list[float]] = {"low": [], "mid": [], "high": []}
        self._last_decision: TradeDecision | None = None
        self._last_costs_bps: dict[str, float] = {}

    def _upgrade_market_maker(self):
        surface = SpreadSurface(
            as_gamma=self.strategy.as_gamma,
            as_kappa=self.settings.as_kappa,
            vol_factor=self.strategy.spread_vol_factor,
            inventory_factor=self.strategy.spread_inventory_factor,
            ofi_factor=self.settings.spread_ofi_factor,
            min_bps=self.settings.spread_min_bps,
            max_bps=self.settings.spread_max_bps,
        )
        from .market_maker import AlwaysOnMarketMaker

        return AlwaysOnMarketMaker(self.settings, surface)

    def _quote(self, tick: ReplayTick) -> QuoteSnapshot:
        return QuoteSnapshot(bid=tick.bid, ask=tick.ask, timestamp=tick.ts)

    def _current_pnl(self, mid: float) -> float:
        unrealized = mark_to_market_unrealized_pnl(self.state, mid)
        return (
            self.state.realized_pnl_usd
            - self.state.estimated_fees_usd
            - self.state.estimated_slippage_usd
            + self.state.funding_pnl_usd
            + unrealized
        )

    def _record_trade(self, fill: FillResult, impact, regime: str, ofi_score: float, queue_position: str, p_toxic: float, quote_mid: float) -> None:
        spread_capture = impact.realized_pnl_usd + impact.fee_usd + impact.slippage_usd
        notional = max(fill.price * fill.qty, 1e-9)
        fee_bps = (impact.fee_usd / notional) * 10000.0
        slippage_bps = (impact.slippage_usd / notional) * 10000.0
        adverse_bps = self._last_costs_bps.get("adverse_selection_bps", 0.0)
        realized_net_bps = (impact.realized_pnl_usd - impact.fee_usd - impact.slippage_usd) / notional * 10000.0
        expected_net_bps = self._last_decision.expected_net_bps if self._last_decision else 0.0
        confidence = self._last_decision.confidence if self._last_decision else 0.0
        self.analytics.record_fill(
            realized_pnl=impact.realized_pnl_usd,
            spread_capture=spread_capture,
            ofi_score=ofi_score,
            regime=regime,
            vol_bps=0.0,
            queue_position=queue_position,
            side=fill.side,
            expected_net_bps=expected_net_bps,
            realized_net_bps=realized_net_bps,
            confidence=confidence,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            adverse_selection_bps=adverse_bps,
            expected_fill_prob=self._last_costs_bps.get("fill_prob", 0.0),
        )
        self.exec_quality.on_fill(impact.slippage_usd)
        self._trades.append(
            BacktestTrade(
                ts=self.state.last_trade_time.isoformat() if self.state.last_trade_time else datetime.now(timezone.utc).isoformat(),
                side=fill.side,
                qty=fill.qty,
                limit_price=fill.limit_price,
                fill_price=fill.price,
                realized_pnl_usd=impact.realized_pnl_usd,
                fee_usd=impact.fee_usd,
                slippage_usd=impact.slippage_usd,
                regime=regime,
                ofi_score=ofi_score,
                queue_position=queue_position,
                p_toxic=p_toxic,
                expected_net_bps=expected_net_bps,
                realized_net_bps=realized_net_bps,
                confidence=confidence,
                threshold_used=self._last_decision.threshold_used if self._last_decision else 0.0,
            )
        )
        if p_toxic >= self.settings.bayes_toxic_threshold:
            adverse = quote_mid - fill.price if fill.side == "buy" else fill.price - quote_mid
            self._toxicity_events.append((p_toxic, adverse))

    def _manage_leg(self, side: str, manager: OrderManager, desired_price: float, desired_qty: float, now: datetime, current_quote: QuoteSnapshot) -> None:
        if side == "sell":
            desired_qty = min(desired_qty, max(self.state.position.qty_btc, 0.0)) if self.state.position.qty_btc > 0 else desired_qty

        if desired_qty <= 0 or desired_price <= 0:
            manager.cancel_pending()
            return

        if not manager.has_pending():
            order = manager.submit(side, desired_qty, desired_price)
            if order is not None and manager.pending is not None:
                manager.pending.submitted_at = now
                self.microstructure.on_order_submitted()
                self.exec_quality.on_submitted()
            return

        pending = manager.pending
        if pending is None:
            return

        age_seconds = manager.pending_age_seconds(now)
        price_drift_bps = abs((desired_price - pending.limit_price) / max(pending.limit_price, 1e-9)) * 10000.0
        qty_drift = abs(desired_qty - pending.qty)

        if price_drift_bps >= self.settings.market_maker_reprice_bps or age_seconds >= self.settings.order_reprice_seconds or qty_drift > 1e-9:
            replaced = manager.replace_pending(desired_price)
            self.microstructure.on_cancel_or_replace()
            if replaced is not None and manager.pending is not None:
                manager.pending.submitted_at = now
                self.microstructure.on_order_submitted()
                self.exec_quality.on_submitted()

    def _apply_fills(self, fills: Sequence[FillResult], tick: ReplayTick) -> None:
        for fill in fills:
            impact = apply_fill_to_state(
                self.state,
                fill.side,
                fill.qty,
                fill.price,
                fill.limit_price,
                fee_rate=0.0004,
                now=tick.ts,
            )
            self.microstructure.on_fill(fill.side, fill.price, tick.mid)
            self._record_trade(
                fill,
                impact,
                regime=tick.regime,
                ofi_score=tick.ofi_score,
                queue_position=self.microstructure.queue.position,
                p_toxic=tick.p_toxic,
                quote_mid=tick.mid,
            )
            if impact.realized_pnl_usd < 0:
                self.state.consecutive_losses = max(self.state.consecutive_losses, 1)
            self.state.last_quote = QuoteSnapshot(bid=tick.bid, ask=tick.ask, timestamp=tick.ts)

    def run(self, ticks: Sequence[ReplayTick]) -> BacktestReport:
        previous_mid: float | None = None
        for tick in ticks:
            quote = self._quote(tick)
            self.state.last_quote = quote
            self.local_book.apply_snapshot(bids=[(quote.bid, 1.0)], asks=[(quote.ask, 1.0)])
            ms = self.microstructure.update(quote.bid, quote.ask)
            regime = self.regime_detector.update(quote.mid)
            data_age = float(tick.raw.get("stream_health", {}).get("data_age_seconds", 0.0)) if tick.raw else 0.0

            if previous_mid is not None:
                move = ((quote.mid - previous_mid) / max(previous_mid, 1e-9)) * 10000.0
                self._ofi_future_moves.append((tick.ofi_score, move))
            previous_mid = quote.mid

            fills = self.bid_orders.reconcile(current_quote=quote) + self.ask_orders.reconcile(current_quote=quote)
            if fills:
                self._apply_fills(fills, tick)

            blocked, reason = self.adverse_guard.update_and_check(quote.mid, tick.ts)
            if blocked:
                self.bid_orders.cancel_pending()
                self.ask_orders.cancel_pending()
                self._halting_reason = reason
                continue

            if self.strategy.upgraded and ms.should_liquidate:
                self._liq_mode = True
                self.bid_orders.cancel_pending()
                self.ask_orders.cancel_pending()
                if self.state.position.qty_btc != 0:
                    exit_side = "sell" if self.state.position.qty_btc > 0 else "buy"
                    qty = abs(self.state.position.qty_btc)
                    price = quote.bid * 0.9998 if exit_side == "sell" else quote.ask * 1.0002
                    manager = self.ask_orders if exit_side == "sell" else self.bid_orders
                    if not manager.has_pending():
                        order = manager.submit(exit_side, qty, price)
                        if order is not None and manager.pending is not None:
                            manager.pending.submitted_at = tick.ts
                            self.microstructure.on_order_submitted()
                continue

            daily_net = (
                self.state.daily_realized_pnl_usd
                - self.state.daily_estimated_fees_usd
                - self.state.daily_estimated_slippage_usd
                + self.state.daily_funding_pnl_usd
            )
            if daily_net <= -abs(self.settings.max_daily_loss_usd):
                self._halting_reason = "max_daily_loss"
                self.bid_orders.cancel_pending()
                self.ask_orders.cancel_pending()
                break
            if self.state.consecutive_losses >= self.settings.max_consecutive_losses:
                self._halting_reason = "max_consecutive_losses"
                self.bid_orders.cancel_pending()
                self.ask_orders.cancel_pending()
                break

            if self._liq_mode and self.state.position.qty_btc == 0:
                self._liq_mode = False

            if self.strategy.upgraded:
                extra_inventory = self.settings.market_maker_inventory_skew_bps if abs(ms.ofi_score) >= 0.6 else 0.0
                ofi_score = ms.ofi_score * self.calibrator.ofi_skew_bps
                momentum_bps = ms.momentum.composite_bps * self.settings.as_momentum_factor
                plan = self.market_maker.build_plan(
                    quote,
                    self.state.position,
                    volatility_bps=ms.vol_bps,
                    regime=regime.regime,
                    spread_multiplier=1.0,
                    size_multiplier=1.0,
                    extra_inventory_skew_bps=extra_inventory,
                    ofi_score=ofi_score,
                    momentum_bps=momentum_bps,
                    queue_position=ms.queue_position,
                )
                if plan is None:
                    continue
                spread_bps = plan.half_spread_bps * 2.0
                self._spread_buckets["low" if ms.vol_bps < 1.5 else "mid" if ms.vol_bps < 4.0 else "high"].append(spread_bps)
                queue_position = ms.queue_position
                inventory_ratio = plan.inventory_ratio
                ofi_input = ms.ofi_score
                momentum_input = ms.momentum.composite_bps
                tox_prob = ms.bayes_p_toxic
            else:
                plan = self.market_maker.build_plan(quote, self.state.position)
                if plan is None:
                    continue
                spread_bps = plan["half_spread_bps"] * 2.0
                self._spread_buckets["low" if regime.volatility_bps < 1.5 else "mid" if regime.volatility_bps < 4.0 else "high"].append(spread_bps)
                queue_position = "unknown"
                inventory_ratio = plan["inventory_ratio"]
                ofi_input = 0.0
                momentum_input = 0.0
                tox_prob = 0.2

            fill_prob = self.decision_policy.estimate_fill_probability(regime.regime, queue_position, self.analytics.fill_rate)
            adverse_bps = 0.35 if regime.regime == "high_vol" else 0.22 if regime.regime == "trend" else 0.10
            slippage_bps = 0.35 if queue_position == "back" else 0.18
            fee_bps = self.settings.market_maker_reprice_bps if self.strategy.upgraded else 0.0
            uncertainty = min(max(regime.volatility_bps / 10.0, 0.0), 1.0)
            decision_input = DecisionInput(
                expected_capture_bps=spread_bps,
                spread_half_bps=spread_bps / 2.0,
                ofi_score=ofi_input,
                momentum_bps=momentum_input,
                regime=regime.regime,
                queue_position=queue_position,
                inventory_ratio=inventory_ratio,
                estimated_fill_prob=fill_prob,
                adverse_selection_bps=adverse_bps,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                uncertainty=uncertainty,
                toxicity_prob=tox_prob,
            )
            decision = self.decision_policy.evaluate(decision_input)
            self.analytics.record_decision(
                regime=regime.regime,
                should_trade=decision.should_trade,
                expected_net_bps=decision.expected_net_bps,
                threshold_bps=decision.threshold_used,
                confidence=decision.confidence,
                reason=decision.reason,
            )
            self._last_decision = decision
            self._last_costs_bps = {
                "fee_bps": fee_bps,
                "slippage_bps": slippage_bps,
                "adverse_selection_bps": adverse_bps,
                "fill_prob": fill_prob,
            }
            if not decision.should_trade:
                self._edge_blocks += 1
                self.exec_quality.on_rejected()
                self.bid_orders.cancel_pending()
                self.ask_orders.cancel_pending()
                continue

            if self.strategy.upgraded:
                if self.state.trade_count > 0 and self.state.trade_count % self.settings.self_cal_every_n_fills == 0:
                    self.calibrator._run_calibration(
                        self.analytics.snapshot(),
                        policy=self.decision_policy,
                        outcomes=self.analytics.decision_outcomes(),
                        artifact_dir=Path("runtime/calibration"),
                    )

            if self.strategy.upgraded:
                bid_price = plan.bid_price
                ask_price = plan.ask_price
                bid_qty = plan.bid_qty
                ask_qty = plan.ask_qty
            else:
                bid_price = plan["bid_price"]
                ask_price = plan["ask_price"]
                bid_qty = plan["bid_qty"]
                ask_qty = plan["ask_qty"]

            self._manage_leg("buy", self.bid_orders, bid_price, bid_qty, tick.ts, quote)
            self._manage_leg("sell", self.ask_orders, ask_price, ask_qty, tick.ts, quote)

            if self.bid_orders.has_pending():
                fills = self.bid_orders.reconcile(current_quote=quote)
                if fills:
                    self._apply_fills(fills, tick)
            if self.ask_orders.has_pending():
                fills = self.ask_orders.reconcile(current_quote=quote)
                if fills:
                    self._apply_fills(fills, tick)

            self._equity_curve.append(self._current_pnl(quote.mid))

        metrics = self._metrics()
        return BacktestReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            source="replay",
            strategy_name=self.strategy.name,
            parameters={
                "upgraded": self.strategy.upgraded,
                "as_gamma": self.strategy.as_gamma,
                "spread_vol_factor": self.strategy.spread_vol_factor,
                "spread_inventory_factor": self.strategy.spread_inventory_factor,
                "ofi_skew_bps": self.strategy.ofi_skew_bps,
                "min_net_edge_bps": self.strategy.min_net_edge_bps,
            },
            metrics=metrics,
            trades=self._trades,
        )

    def _metrics(self) -> BacktestMetrics:
        trade_pnls = [trade.realized_pnl_usd - trade.fee_usd - trade.slippage_usd for trade in self._trades]
        total_pnl = sum(trade_pnls)
        sharpe = _sharpe(trade_pnls)
        max_dd = _max_drawdown_pct(self._equity_curve)
        win_rate = (sum(1 for pnl in trade_pnls if pnl > 0) / len(trade_pnls)) if trade_pnls else 0.0
        fill_rate = self.exec_quality.filled / max(self.exec_quality.submitted, 1)
        avg_spread_capture = self.analytics.avg_spread_capture_bps if self._trades else 0.0
        ofi_validation = _ofi_validation(self._ofi_future_moves)
        toxicity_validation = _toxicity_validation(self._trades)
        spread_validation = _spread_validation(self._spread_buckets)
        return BacktestMetrics(
            total_pnl_usd=round(total_pnl, 6),
            sharpe_ratio=round(sharpe, 4),
            max_drawdown_pct=round(max_dd, 4),
            win_rate=round(win_rate, 4),
            fill_rate=round(fill_rate, 4),
            avg_spread_capture_bps=round(avg_spread_capture, 4),
            total_trades=len(self._trades),
            total_fills=self.exec_quality.filled,
            edge_blocks=self._edge_blocks,
            toxicity_vetoes=self._toxicity_vetoes,
            equity_curve=[round(x, 6) for x in self._equity_curve[-500:]],
            regime_pnl=self.analytics.regime_pnl_summary(),
            queue_pnl=self.analytics.queue_pnl_summary(),
            ofi_validation=ofi_validation,
            toxicity_validation=toxicity_validation,
            spread_validation=spread_validation,
        )


def _sharpe(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    sigma = pstdev(values)
    if sigma < 1e-12:
        return 0.0
    return (avg / sigma) * math.sqrt(len(values))


def _max_drawdown_pct(equity_curve: Sequence[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak <= 0:
            continue
        drawdown = (peak - equity) / max(abs(peak), 1e-9) * 100.0
        max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown


def _ofi_validation(samples: Sequence[tuple[float, float]]) -> dict[str, float]:
    positive_moves = [move for ofi, move in samples if ofi > 0.3]
    negative_moves = [move for ofi, move in samples if ofi < -0.3]
    return {
        "positive_sample_count": float(len(positive_moves)),
        "negative_sample_count": float(len(negative_moves)),
        "avg_forward_move_pos_bps": round(mean(positive_moves), 4) if positive_moves else 0.0,
        "avg_forward_move_neg_bps": round(mean(negative_moves), 4) if negative_moves else 0.0,
    }


def _toxicity_validation(trades: Sequence[BacktestTrade]) -> dict[str, float]:
    toxic = [t for t in trades if t.p_toxic >= 0.7]
    if not toxic:
        return {"toxic_sample_count": 0.0, "avg_toxic_trade_pnl_usd": 0.0}
    toxic_pnl = [t.realized_pnl_usd - t.fee_usd - t.slippage_usd for t in toxic]
    return {
        "toxic_sample_count": float(len(toxic)),
        "avg_toxic_trade_pnl_usd": round(mean(toxic_pnl), 6),
    }


def _spread_validation(buckets: dict[str, list[float]]) -> dict[str, float]:
    low = buckets.get("low", [])
    mid = buckets.get("mid", [])
    high = buckets.get("high", [])
    return {
        "avg_spread_low_vol": round(mean(low), 4) if low else 0.0,
        "avg_spread_mid_vol": round(mean(mid), 4) if mid else 0.0,
        "avg_spread_high_vol": round(mean(high), 4) if high else 0.0,
    }


def _payload_to_tick(payload: dict[str, object], raw: dict[str, object]) -> ReplayTick | None:
    try:
        ts_raw = payload.get("quote_timestamp") or raw.get("ts") or raw.get("timestamp")
        if ts_raw is None:
            return None
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        bid = float(payload.get("bid") or 0.0)
        ask = float(payload.get("ask") or 0.0)
        mid = float(payload.get("price") or payload.get("book_mid") or 0.0)
        spread_bps = float(payload.get("book_spread_bps") or 0.0)
        if bid <= 0 or ask <= 0:
            if mid > 0 and spread_bps > 0:
                spread = mid * spread_bps / 10000.0
                bid = mid - spread / 2.0
                ask = mid + spread / 2.0
            elif mid > 0:
                bid = mid * 0.9999
                ask = mid * 1.0001
        if mid <= 0:
            mid = (bid + ask) / 2.0
        return ReplayTick(
            ts=ts,
            bid=bid,
            ask=ask,
            price=mid,
            regime=str(payload.get("regime") or "unknown"),
            volatility_bps=float(payload.get("volatility_bps") or 0.0),
            ofi_score=float(payload.get("ofi_score") or 0.0),
            p_toxic=float(payload.get("p_toxic") or 0.0),
            bayes_regime=str(payload.get("bayes_regime") or "unknown"),
            liquidation_mode=bool(payload.get("liquidation_mode") or False),
            analytics=dict(payload.get("analytics") or {}),
            raw=raw,
        )
    except Exception:
        return None


def load_replay_ticks(source: Path, limit: int | None = None) -> list[ReplayTick]:
    if source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return _load_replay_ticks_from_sqlite(source, limit=limit)
    return _load_replay_ticks_from_log(source, limit=limit)


def _load_replay_ticks_from_sqlite(source: Path, limit: int | None = None) -> list[ReplayTick]:
    if not source.exists():
        return []
    conn = sqlite3.connect(str(source))
    try:
        rows = conn.execute(
            "SELECT ts, payload_json FROM events WHERE event_type='heartbeat' ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()
    ticks: list[ReplayTick] = []
    for ts, payload_json in rows[-limit:] if limit else rows:
        try:
            payload = json.loads(payload_json)
        except Exception:
            continue
        raw = {"ts": ts}
        tick = _payload_to_tick(payload, raw)
        if tick is not None:
            ticks.append(tick)
    return ticks


def _load_replay_ticks_from_log(source: Path, limit: int | None = None) -> list[ReplayTick]:
    if not source.exists():
        return []
    ticks: list[ReplayTick] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if payload.get("event") != "heartbeat":
                continue
            tick = _payload_to_tick(payload, payload)
            if tick is not None:
                ticks.append(tick)
    if limit is not None:
        return ticks[-limit:]
    return ticks


def compare_strategies(ticks: Sequence[ReplayTick], settings: Settings, seed: int = 7) -> dict[str, BacktestReport]:
    baseline = BacktestEngine(settings, StrategyParams("baseline", False, settings.as_gamma, 0.0, 0.0, 0.0, 0.0), seed=seed)
    upgraded = BacktestEngine(
        settings,
        StrategyParams(
            "upgraded",
            True,
            settings.as_gamma,
            settings.spread_vol_factor,
            settings.spread_inventory_factor,
            settings.ofi_skew_bps,
            settings.market_maker_target_spread_bps,
        ),
        seed=seed,
    )
    return {"baseline": baseline.run(ticks), "upgraded": upgraded.run(ticks)}


def sweep_parameters(ticks: Sequence[ReplayTick], settings: Settings, grid: dict[str, Sequence[float]], seed: int = 7) -> list[dict]:
    results: list[dict] = []
    for as_gamma in grid.get("AS_GAMMA", [settings.as_gamma]):
        for spread_vol_factor in grid.get("SPREAD_VOL_FACTOR", [settings.spread_vol_factor]):
            for spread_inventory_factor in grid.get("SPREAD_INVENTORY_FACTOR", [settings.spread_inventory_factor]):
                for ofi_skew_bps in grid.get("OFI_SKEW_BPS", [settings.ofi_skew_bps]):
                    for min_net_edge_bps in grid.get("MIN_NET_EDGE_BPS", [settings.min_net_edge_bps]):
                        runner = BacktestEngine(
                            settings,
                            StrategyParams(
                                name="grid",
                                upgraded=True,
                                as_gamma=as_gamma,
                                spread_vol_factor=spread_vol_factor,
                                spread_inventory_factor=spread_inventory_factor,
                                ofi_skew_bps=ofi_skew_bps,
                                min_net_edge_bps=min_net_edge_bps,
                            ),
                            seed=seed,
                        )
                        report = runner.run(ticks)
                        results.append(
                            {
                                "params": {
                                    "AS_GAMMA": as_gamma,
                                    "SPREAD_VOL_FACTOR": spread_vol_factor,
                                    "SPREAD_INVENTORY_FACTOR": spread_inventory_factor,
                                    "OFI_SKEW_BPS": ofi_skew_bps,
                                    "MIN_NET_EDGE_BPS": min_net_edge_bps,
                                },
                                "metrics": report.metrics,
                                "score": _score_metrics(report.metrics),
                            }
                        )
    results.sort(key=lambda item: item["score"], reverse=True)
    return results


def _score_metrics(metrics: BacktestMetrics) -> float:
    return (
        metrics.total_pnl_usd
        + metrics.win_rate * 10.0
        - metrics.max_drawdown_pct * 1.5
        + metrics.sharpe_ratio * 2.0
        - metrics.edge_blocks * 0.25
    )


def build_backtest_report(source: Path, settings: Settings, output_dir: Path | None = None, limit: int | None = None) -> dict:
    ticks = load_replay_ticks(source, limit=limit)
    if not ticks:
        raise ValueError(f"No replay ticks found in {source}")

    comparison = compare_strategies(ticks, settings)
    sweep_grid = {
        "AS_GAMMA": [max(settings.as_gamma * factor, 0.02) for factor in (0.5, 1.0, 1.5)],
        "SPREAD_VOL_FACTOR": [max(settings.spread_vol_factor * factor, 0.05) for factor in (0.5, 1.0, 1.5)],
        "SPREAD_INVENTORY_FACTOR": [max(settings.spread_inventory_factor * factor, 0.1) for factor in (0.5, 1.0, 1.5)],
        "OFI_SKEW_BPS": [max(settings.ofi_skew_bps * factor, 0.1) for factor in (0.5, 1.0, 1.5)],
        "MIN_NET_EDGE_BPS": [max(settings.min_net_edge_bps * factor, 0.1) for factor in (0.75, 1.0, 1.25)],
    }
    sweep_results = sweep_parameters(ticks, settings, sweep_grid)
    best = sweep_results[0] if sweep_results else None
    baseline = comparison["baseline"]
    upgraded = comparison["upgraded"]
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "ticks": len(ticks),
        "comparison": {
            "baseline": baseline.metrics.to_dict(),
            "upgraded": upgraded.metrics.to_dict(),
            "baseline_pnl": baseline.metrics.total_pnl_usd,
            "upgraded_pnl": upgraded.metrics.total_pnl_usd,
            "delta_pnl": round(upgraded.metrics.total_pnl_usd - baseline.metrics.total_pnl_usd, 6),
            "better": upgraded.metrics.total_pnl_usd > baseline.metrics.total_pnl_usd,
        },
        "best_parameter_set": best["params"] if best else {},
        "best_parameter_score": best["score"] if best else 0.0,
        "sweep_top5": [
            {
                "params": row["params"],
                "score": row["score"],
                "total_pnl_usd": row["metrics"].total_pnl_usd,
                "sharpe_ratio": row["metrics"].sharpe_ratio,
                "max_drawdown_pct": row["metrics"].max_drawdown_pct,
                "win_rate": row["metrics"].win_rate,
                "fill_rate": row["metrics"].fill_rate,
            }
            for row in sweep_results[:5]
        ],
        "insights": {
            "ofi_validation": upgraded.metrics.ofi_validation,
            "toxicity_validation": upgraded.metrics.toxicity_validation,
            "spread_validation": upgraded.metrics.spread_validation,
            "regime_pnl": upgraded.metrics.regime_pnl,
            "queue_pnl": upgraded.metrics.queue_pnl,
        },
    }

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        target.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        out["report_path"] = str(target)
    return out


def _default_source_path() -> Path:
    candidates = [Path("runtime/logs/bot.log"), Path("runtime/trades.db")]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No replay source found. Expected runtime/logs/bot.log or runtime/trades.db")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run HFT backtest replay on recorded heartbeat ticks")
    parser.add_argument("--source", type=Path, default=_default_source_path())
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/backtests"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    from .config import load_and_validate_settings

    settings = load_and_validate_settings()
    report = build_backtest_report(args.source, settings, output_dir=args.output_dir, limit=args.limit)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
