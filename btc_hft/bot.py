from __future__ import annotations

import logging
import os
import signal
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from statistics import median
from time import perf_counter_ns

from .adapters import AdapterFactory
from .alerts import AlertConfig, AlertDispatcher
from .auto_ops import AutoOpsGuard
from .config import Settings
from .database import Database
from .latency import LocalOrderBookEngine
from .market_maker import AlwaysOnMarketMaker
from .models import PositionState, RuntimeState
from .order_manager import FillResult, OrderManager
from .portfolio import apply_fill_to_state, apply_funding_to_state, mark_to_market_unrealized_pnl
from .profit_controls import (
    AdverseSelectionGuard,
    ExecutionQualityMonitor,
    NetEdgeGate,
    RegimeDetector,
    build_pnl_attribution,
)
from .reporting import write_end_of_day_report
from .risk import RiskEngine
from .session import SessionGuard

logger = logging.getLogger(__name__)


class Bot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.db_path)
        self.state = RuntimeState(position=PositionState())

        # Phase 0: Use ExchangeAdapter pattern (currently AlpacaAdapter by default)
        # Phase 1+: Can switch between exchanges via config
        exchange_name = os.getenv("EXCHANGE", "alpaca").lower()
        self.adapter = AdapterFactory.create(exchange_name, settings=settings)
        
        # Access adapter's underlying services for backward compatibility
        # (Adapters wrap the sync threading-based implementations)
        self.market = self.adapter.market_data
        
        self.trading = None if settings.dry_run else self.adapter.trading
        if self.trading is not None:
            self.trading.validate_paper_balance(settings.max_trade_notional_usd)
        
        self.bid_orders = OrderManager(self.trading, dry_run=settings.dry_run)
        self.ask_orders = OrderManager(self.trading, dry_run=settings.dry_run)
        self.market_maker = AlwaysOnMarketMaker(settings)
        self.risk = RiskEngine(settings)
        self.session = SessionGuard(settings)
        self.fast_mode_enabled = os.getenv("FAST_MODE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
        fast_loop_ms = float(os.getenv("FAST_LOOP_INTERVAL_MS", "100"))
        self.runtime_loop_interval_seconds = (
            max(0.005, fast_loop_ms / 1000.0) if self.fast_mode_enabled else self.settings.loop_interval_seconds
        )
        self.local_book = LocalOrderBookEngine(symbol=self.settings.trading_symbol)
        self.loop_latency_us: deque[float] = deque(maxlen=500)
        self._last_latency_report_at: datetime | None = None
        self.regime_detector = RegimeDetector(lookback=int(os.getenv("REGIME_LOOKBACK_TICKS", "40")))
        self.edge_gate = NetEdgeGate(min_net_edge_bps=float(os.getenv("MIN_NET_EDGE_BPS", "0.35")))
        self.adverse_guard = AdverseSelectionGuard(
            move_bps_threshold=float(os.getenv("ADVERSE_MOVE_BPS", "6.0")),
            cooldown_seconds=int(os.getenv("ADVERSE_COOLDOWN_SECONDS", "2")),
        )
        self.exec_quality = ExecutionQualityMonitor()
        self.min_fill_ratio = float(os.getenv("MIN_FILL_RATIO", "0.08"))
        self.max_reject_ratio = float(os.getenv("MAX_REJECT_RATIO", "0.25"))
        self.max_avg_slippage_usd = float(os.getenv("MAX_AVG_SLIPPAGE_USD", "2.0"))
        self.slippage_notional_floor_usd = float(os.getenv("SLIPPAGE_NOTIONAL_FLOOR_USD", "100.0"))
        self.edge_fee_bps = float(os.getenv("EDGE_FEE_BPS_OVERRIDE", str(self.adapter.maker_fee_bps)))
        self.edge_capture_multiplier = float(os.getenv("EDGE_CAPTURE_MULTIPLIER", "2.0"))
        self.inventory_accel_ratio = float(os.getenv("INVENTORY_ACCEL_RATIO", "0.6"))
        self.derisk_spread_multiplier = float(os.getenv("DERISK_SPREAD_MULTIPLIER", "1.8"))
        self.derisk_size_multiplier = float(os.getenv("DERISK_SIZE_MULTIPLIER", "0.4"))
        self.auto_ops_enabled = os.getenv("AUTO_OPS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
        self.daily_auto_report_enabled = os.getenv("DAILY_AUTO_REPORT_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
        self.auto_ops = AutoOpsGuard(
            stale_data_seconds=settings.stale_data_seconds,
            max_fill_slippage_usd=float(os.getenv("MAX_FILL_SLIPPAGE_USD", "2.50")),
        )
        self.alerts = AlertDispatcher(
            AlertConfig(
                channel=os.getenv("ALERT_CHANNEL", "disabled"),
                webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""),
                telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
                smtp_host=os.getenv("ALERT_SMTP_HOST", ""),
                smtp_port=int(os.getenv("ALERT_SMTP_PORT", "587")),
                smtp_user=os.getenv("ALERT_SMTP_USER", ""),
                smtp_password=os.getenv("ALERT_SMTP_PASSWORD", ""),
                email_from=os.getenv("ALERT_EMAIL_FROM", ""),
                email_to=os.getenv("ALERT_EMAIL_TO", ""),
            )
        )
        self._last_dashboard_at: datetime | None = None
        self._last_restart_request_at: datetime | None = None

        self._running = True
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

    def _stop(self, *_args) -> None:
        self._running = False

    def _log_event(self, event_type: str, payload: dict) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        self.db.log_event(ts, event_type, payload)
        if event_type == "order_rejected":
            self.exec_quality.on_rejected()
        if event_type in {"risk_block", "stream_restart_requested", "order_rejected", "auto_ops_stop"}:
            self._emit_alert(f"BTC HFT Alert: {event_type}", str(payload))

    def _emit_alert(self, title: str, message: str) -> None:
        try:
            sent = self.alerts.send(title, message)
            if sent:
                self.db.log_event(datetime.now(timezone.utc).isoformat(), "alert_sent", {"title": title, "message": message})
        except Exception as exc:
            logger.warning("Alert dispatch failed", extra={"event": "alert_failed", "reason": str(exc)})

    def _apply_fill(self, fill: FillResult) -> None:
        now = datetime.now(timezone.utc)
        impact = apply_fill_to_state(
            self.state,
            side=fill.side,
            qty=fill.qty,
            fill_price=fill.price,
            limit_price=fill.limit_price,
            fee_rate=0.0002,
            now=now,
        )

        if impact.realized_pnl_usd < 0:
            self.risk.trigger_cooldown()

        self.db.log_fill(
            ts=now.isoformat(),
            symbol=self.settings.symbol,
            side=fill.side,
            qty=fill.qty,
            price=fill.price,
            client_order_id=fill.client_order_id,
            order_id=fill.order_id,
            realized_pnl_usd=impact.realized_pnl_usd,
            est_fee_usd=impact.fee_usd,
            est_slippage_usd=impact.slippage_usd,
            funding_pnl_usd=impact.funding_pnl_usd,
        )

        logger.info(
            "Fill applied",
            extra={
                "event": "fill",
                "side": fill.side,
                "qty": fill.qty,
                "price": fill.price,
                "limit_price": fill.limit_price,
                "status": fill.status,
                "is_partial": fill.is_partial,
                "order_id": fill.order_id,
                "client_order_id": fill.client_order_id,
            },
        )
        self.exec_quality.on_fill(impact.slippage_usd)

        attribution = build_pnl_attribution(
            realized_usd=impact.realized_pnl_usd,
            fees_usd=impact.fee_usd,
            slippage_usd=impact.slippage_usd,
            funding_usd=impact.funding_pnl_usd,
        )
        self._log_event(
            "pnl_attribution",
            {
                "order_id": fill.order_id,
                "spread_capture_usd": attribution.spread_capture_usd,
                "fees_usd": attribution.fees_usd,
                "slippage_usd": attribution.slippage_usd,
                "funding_usd": attribution.funding_usd,
                "realized_usd": attribution.realized_usd,
            },
        )

        if self.auto_ops_enabled:
            decision = self.auto_ops.check_fill_slippage(impact.slippage_usd)
            if decision.should_stop:
                self._log_event(
                    "auto_ops_stop",
                    {
                        "reason": decision.reason,
                        "slippage_usd": impact.slippage_usd,
                        "order_id": fill.order_id,
                    },
                )
                self._running = False

    def _entry_limit_price(self, side: str, bid: float, ask: float) -> float:
        offset = self.settings.order_price_offset_bps / 10000
        if side == "buy":
            return ask * (1 + offset)
        return bid * (1 - offset)

    def _sellable_btc(self) -> float:
        local_qty = max(self.state.position.qty_btc, 0.0)
        if self.settings.dry_run or self.trading is None:
            return local_qty

        get_available = getattr(self.trading, "get_available_btc", None)
        if not callable(get_available):
            return local_qty

        try:
            broker_qty = max(float(get_available(self.settings.trading_symbol)), 0.0)
        except Exception as exc:
            logger.warning("Failed to read broker available BTC", extra={"event": "available_btc_error", "reason": str(exc)})
            return local_qty

        return min(local_qty, broker_qty)

    def _unrealized_pnl(self, mid: float) -> float:
        return mark_to_market_unrealized_pnl(self.state, mid)

    def _update_local_book(self, bid: float, ask: float) -> None:
        if bid <= 0 or ask <= 0:
            return
        self.local_book.apply_snapshot(bids=[(bid, 1.0)], asks=[(ask, 1.0)])

    def _record_loop_latency(self, start_ns: int, end_ns: int) -> None:
        elapsed_us = (end_ns - start_ns) / 1000.0
        self.loop_latency_us.append(elapsed_us)

        now = datetime.now(timezone.utc)
        if self._last_latency_report_at is None:
            self._last_latency_report_at = now
            return

        if (now - self._last_latency_report_at).total_seconds() < 10:
            return

        values = sorted(self.loop_latency_us)
        if values:
            p95_idx = int(0.95 * (len(values) - 1))
            logger.info(
                "Loop latency summary",
                extra={
                    "event": "latency_summary",
                    "loop_interval_seconds": self.runtime_loop_interval_seconds,
                    "samples": len(values),
                    "p50_us": round(float(median(values)), 2),
                    "p95_us": round(float(values[p95_idx]), 2),
                    "max_us": round(float(values[-1]), 2),
                },
            )
        self._last_latency_report_at = now

    def _render_dashboard(self, mid: float, data_age: float, signal_text: str) -> None:
        if not self.settings.dashboard_enabled:
            return

        now = datetime.now(timezone.utc)
        if self._last_dashboard_at:
            delta = (now - self._last_dashboard_at).total_seconds()
            if delta < self.settings.dashboard_interval_seconds:
                return

        self._last_dashboard_at = now
        pos = self.state.position
        unrealized = self._unrealized_pnl(mid)
        net = self.state.realized_pnl_usd - self.state.estimated_fees_usd - self.state.estimated_slippage_usd + self.state.funding_pnl_usd + unrealized

        os.system("cls")
        print("BTC HFT Paper Dashboard")
        print("=" * 36)
        print(f"Mode: {'DRY_RUN' if self.settings.dry_run else 'PAPER_LIVE_API'}")
        print(f"Symbol: {self.settings.symbol} | Mid: {mid:.2f} | Data age: {data_age:.2f}s")
        print(f"Position: {pos.qty_btc:.6f} BTC @ {pos.avg_entry_price:.2f} | Side: {pos.side}")
        print(
            "PnL USD: realized={:.4f} unrealized={:.4f} fees={:.4f} slippage={:.4f} funding={:.4f} net={:.4f}".format(
                self.state.realized_pnl_usd,
                unrealized,
                self.state.estimated_fees_usd,
                self.state.estimated_slippage_usd,
                self.state.funding_pnl_usd,
                net,
            )
        )
        print(
            f"Trades: {self.state.trade_count} (daily {self.state.daily_trade_count}) | W/L: {self.state.wins}/{self.state.losses} | "
            f"Consecutive losses: {self.state.consecutive_losses}"
        )
        print(f"Signal: {signal_text} | Block: {self.state.blocked_reason or 'none'}")
        print(
            f"Pending orders: bid={'yes' if self.bid_orders.has_pending() else 'no'} | ask={'yes' if self.ask_orders.has_pending() else 'no'}"
        )
        print(f"Stream health: {self.market.health_snapshot()}")

    def _should_exit(self, mid: float, now: datetime) -> tuple[bool, str]:
        pos = self.state.position
        if pos.qty_btc == 0 or pos.entry_time is None:
            return False, ""

        hold_seconds = (now - pos.entry_time).total_seconds()
        if hold_seconds >= self.settings.max_holding_seconds:
            return True, "timeout"

        pnl_bps = 0.0
        if pos.qty_btc > 0:
            pnl_bps = ((mid - pos.avg_entry_price) / pos.avg_entry_price) * 10000
        elif pos.qty_btc < 0:
            pnl_bps = ((pos.avg_entry_price - mid) / pos.avg_entry_price) * 10000

        if pnl_bps >= self.settings.take_profit_bps:
            return True, "take_profit"
        if pnl_bps <= -self.settings.stop_loss_bps:
            return True, "stop_loss"
        return False, ""

    def _apply_funding_if_needed(self, mid: float, now: datetime) -> None:
        apply_funding_to_state(self.state, mid, self.settings.funding_rate_bps_per_hour, now)

    def _maybe_restart_market(self, now: datetime, reason: str) -> None:
        if self._last_restart_request_at is not None and (now - self._last_restart_request_at).total_seconds() < 15:
            return
        self._last_restart_request_at = now
        self.market.request_restart(reason)

    def _manage_quote_leg(self, side: str, manager: OrderManager, desired_price: float, desired_qty: float, now: datetime) -> None:
        if side == "sell":
            available_btc = self._sellable_btc()
            desired_qty = min(desired_qty, available_btc)

        if desired_qty <= 0:
            return

        if not manager.has_pending():
            ok, risk_reason = self.risk.check_new_order(
                self.state,
                desired_qty if side == "buy" else -desired_qty,
                desired_price,
            )
            if ok:
                order = manager.submit(side, desired_qty, desired_price)
                if order is not None:
                    self.exec_quality.on_submitted()
                    self._log_event(
                        "quote_submitted",
                        {"side": side, "qty": desired_qty, "price": desired_price, "order": asdict(order)},
                    )
                else:
                    self._log_event(
                        "order_rejected",
                        {"side": side, "qty": desired_qty, "price": desired_price, "reason": "alpaca_rejected_order"},
                    )
            else:
                self._log_event("risk_block", {"reason": risk_reason, "side": side})
            return

        pending = manager.pending
        if pending is None:
            return

        price_drift_bps = abs(desired_price - pending.limit_price) / max(pending.limit_price, 1e-9) * 10000.0
        age_seconds = manager.pending_age_seconds(now)
        qty_drift = abs(desired_qty - pending.qty)

        if price_drift_bps >= self.settings.market_maker_reprice_bps or age_seconds >= self.settings.order_reprice_seconds or qty_drift > 1e-9:
            replaced = manager.replace_pending(desired_price)
            self.exec_quality.on_canceled_or_replaced()
            if replaced is not None:
                self.exec_quality.on_submitted()
            self._log_event(
                "quote_replaced",
                {
                    "side": side,
                    "price_drift_bps": price_drift_bps,
                    "age_seconds": age_seconds,
                    "qty_drift": qty_drift,
                    "replaced": bool(replaced),
                },
            )

    def run(self) -> None:
        self.market.start()
        logger.info(
            "Bot started",
            extra={
                "event": "bot_started",
                "symbol": self.settings.symbol,
                "reason": "dry_run" if self.settings.dry_run else "paper",
                "fast_mode_enabled": self.fast_mode_enabled,
                "loop_interval_seconds": self.runtime_loop_interval_seconds,
            },
        )

        try:
            while self._running:
                loop_start_ns = perf_counter_ns()
                now = datetime.now(timezone.utc)
                quote = self.market.last_quote
                stream_health = self.market.health_snapshot()
                self._update_local_book(quote.bid, quote.ask)
                data_age = (now - quote.timestamp).total_seconds()
                stream_age = stream_health.get("data_age_seconds") if isinstance(stream_health, dict) else None
                if isinstance(stream_age, (int, float)):
                    data_age = float(stream_age)
                elif isinstance(stream_health, dict) and stream_health.get("last_message_at") is None:
                    # Warmup phase: no quote message received yet.
                    data_age = 0.0
                dashboard_signal_text = "hold"
                regime = self.regime_detector.update(quote.mid)

                paused, pause_reason = self.adverse_guard.update_and_check(quote.mid, now)
                if paused:
                    self.bid_orders.cancel_pending()
                    self.ask_orders.cancel_pending()
                    self._log_event("adverse_selection_pause", {"reason": pause_reason, "regime": regime.regime})
                    self._record_loop_latency(loop_start_ns, perf_counter_ns())
                    time.sleep(self.runtime_loop_interval_seconds)
                    continue

                derisk, derisk_reason, metrics = self.exec_quality.should_derisk(
                    min_fill_ratio=self.min_fill_ratio,
                    max_reject_ratio=self.max_reject_ratio,
                    max_avg_slippage_usd=self.max_avg_slippage_usd,
                )
                spread_multiplier = self.derisk_spread_multiplier if derisk else 1.0
                size_multiplier = self.derisk_size_multiplier if derisk else 1.0
                if derisk:
                    self._log_event(
                        "execution_derisk",
                        {
                            "reason": derisk_reason,
                            "fill_ratio": metrics.fill_ratio,
                            "reject_ratio": metrics.reject_ratio,
                            "avg_slippage_usd": metrics.avg_slippage_usd,
                        },
                    )

                if self.auto_ops_enabled:
                    health_decision = self.auto_ops.check_health(data_age, stream_health)
                    if health_decision.should_stop:
                        self._log_event(
                            "auto_ops_stop",
                            {
                                "reason": health_decision.reason,
                                "data_age": data_age,
                                "stream_health": stream_health,
                            },
                        )
                        self.bid_orders.cancel_pending()
                        self.ask_orders.cancel_pending()
                        self._running = False
                        break

                if self.daily_auto_report_enabled and self.auto_ops.should_emit_daily_report(now):
                    daily = write_end_of_day_report(
                        self.settings.db_path.parent / "reports",
                        self.state,
                        self.settings.symbol,
                        stream_health,
                    )
                    self._log_event("daily_auto_report", {"path": str(daily)})

                session_decision = self.session.evaluate(self.state, now)
                if session_decision.should_stop:
                    self.bid_orders.cancel_pending()
                    self.ask_orders.cancel_pending()
                    self._log_event("session_stop", {"reason": session_decision.reason, "day": session_decision.session_day})
                    self._running = False
                    break

                if quote.mid > 0:
                    self._apply_funding_if_needed(quote.mid, now)

                for fill in self.bid_orders.reconcile() + self.ask_orders.reconcile():
                    self._apply_fill(fill)

                blocked, reason = self.risk.is_blocked(self.state, now, data_age)
                self.state.blocked_reason = reason
                if blocked:
                    self.bid_orders.cancel_pending()
                    self.ask_orders.cancel_pending()
                    self._log_event("risk_block", {"reason": reason, "state": asdict(self.state)})
                    self._render_dashboard(quote.mid, data_age, dashboard_signal_text)
                    self._record_loop_latency(loop_start_ns, perf_counter_ns())
                    time.sleep(self.runtime_loop_interval_seconds)
                    continue

                if data_age > self.settings.stale_data_seconds:
                    self.bid_orders.cancel_pending()
                    self.ask_orders.cancel_pending()
                    if self.auto_ops_enabled:
                        self._log_event("auto_ops_stop", {"reason": "stale_data", "data_age": data_age})
                        self._running = False
                        break
                    self._maybe_restart_market(now, "stale_data")
                    self._log_event("stream_restart_requested", {"reason": "stale_data", "data_age": data_age})
                    self._render_dashboard(quote.mid, data_age, dashboard_signal_text)
                    self._record_loop_latency(loop_start_ns, perf_counter_ns())
                    time.sleep(self.runtime_loop_interval_seconds)
                    continue

                if quote.mid <= 0:
                    self._render_dashboard(quote.mid, data_age, dashboard_signal_text)
                    self._record_loop_latency(loop_start_ns, perf_counter_ns())
                    time.sleep(self.runtime_loop_interval_seconds)
                    continue

                if self.state.position.qty_btc <= 0 and not self.bid_orders.has_pending():
                    bootstrap_price = self._entry_limit_price("buy", quote.bid, quote.ask)
                    ok, risk_reason = self.risk.check_new_order(self.state, self.settings.order_size_btc, bootstrap_price)
                    if ok:
                        order = self.bid_orders.submit("buy", self.settings.order_size_btc, bootstrap_price)
                        if order is not None:
                            self._log_event(
                                "inventory_bootstrap_submitted",
                                {"qty": self.settings.order_size_btc, "price": bootstrap_price, "order": asdict(order)},
                            )
                        else:
                            self._log_event(
                                "order_rejected",
                                {"side": "buy", "qty": self.settings.order_size_btc, "price": bootstrap_price, "reason": "alpaca_rejected_order"},
                            )
                    else:
                        self._log_event("risk_block", {"reason": risk_reason, "side": "buy", "phase": "bootstrap"})
                    self._render_dashboard(quote.mid, data_age, "bootstrap")
                    self._record_loop_latency(loop_start_ns, perf_counter_ns())
                    time.sleep(self.runtime_loop_interval_seconds)
                    continue

                exit_now, exit_reason = self._should_exit(quote.mid, now)
                if exit_now and self.state.position.qty_btc != 0:
                    self.bid_orders.cancel_pending()
                    self.ask_orders.cancel_pending()
                    exit_side = "sell" if self.state.position.qty_btc > 0 else "buy"
                    if exit_side == "sell":
                        qty = self._sellable_btc()
                    else:
                        qty = abs(self.state.position.qty_btc)
                    if qty <= 0:
                        self._log_event("exit_skipped", {"reason": "no_sellable_balance", "exit_reason": exit_reason})
                        self._record_loop_latency(loop_start_ns, perf_counter_ns())
                        time.sleep(self.runtime_loop_interval_seconds)
                        continue
                    price = self._entry_limit_price(exit_side, quote.bid, quote.ask)
                    signed_exit_qty = -qty if exit_side == "sell" else qty
                    ok, risk_reason = self.risk.check_new_order(self.state, signed_exit_qty, price)
                    if ok:
                        exit_manager = self.ask_orders if exit_side == "sell" else self.bid_orders
                        order = exit_manager.submit(exit_side, qty, price)
                        if order is not None:
                            self._log_event("exit_order_submitted", {"reason": exit_reason, "order": asdict(order)})
                        else:
                            self._log_event("order_rejected", {"reason": exit_reason, "side": exit_side, "price": price, "qty": qty})
                    else:
                        self._log_event("risk_block", {"reason": risk_reason})
                    self._render_dashboard(quote.mid, data_age, "exit")
                    self._record_loop_latency(loop_start_ns, perf_counter_ns())
                    time.sleep(self.runtime_loop_interval_seconds)
                    continue

                plan = self.market_maker.build_plan(quote, self.state.position)
                if plan is None:
                    dashboard_signal_text = "no_quote"
                else:
                    extra_inventory_skew = (
                        self.settings.market_maker_inventory_skew_bps
                        if abs(plan.inventory_ratio) >= self.inventory_accel_ratio
                        else 0.0
                    )
                    plan = self.market_maker.build_plan(
                        quote,
                        self.state.position,
                        volatility_bps=regime.volatility_bps,
                        regime=regime.regime,
                        spread_multiplier=spread_multiplier,
                        size_multiplier=size_multiplier,
                        extra_inventory_skew_bps=extra_inventory_skew,
                    )
                    if plan is None:
                        self._record_loop_latency(loop_start_ns, perf_counter_ns())
                        time.sleep(self.runtime_loop_interval_seconds)
                        continue

                    gross_edge_bps = plan.half_spread_bps * max(self.edge_capture_multiplier, 0.1)
                    fee_bps = self.edge_fee_bps
                    quote_notional = quote.mid * max(max(plan.bid_qty, plan.ask_qty), 1e-9)
                    modeled_notional = max(quote_notional, self.slippage_notional_floor_usd)
                    slippage_bps = (self.max_avg_slippage_usd / max(modeled_notional, 1e-9)) * 10000.0
                    adverse_penalty_bps = 0.8 if regime.regime in {"trend", "high_vol"} else 0.2
                    edge_decision = self.edge_gate.evaluate(
                        expected_edge_bps=gross_edge_bps,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                        adverse_selection_bps=adverse_penalty_bps,
                    )
                    if not edge_decision.should_trade:
                        self.bid_orders.cancel_pending()
                        self.ask_orders.cancel_pending()
                        self._log_event(
                            "net_edge_block",
                            {
                                "reason": edge_decision.reason,
                                "net_edge_bps": edge_decision.net_edge_bps,
                                "gross_edge_bps": gross_edge_bps,
                                "fee_bps": fee_bps,
                                "slippage_bps": slippage_bps,
                                "modeled_notional_usd": modeled_notional,
                                "adverse_penalty_bps": adverse_penalty_bps,
                                "regime": regime.regime,
                            },
                        )
                        self._record_loop_latency(loop_start_ns, perf_counter_ns())
                        time.sleep(self.runtime_loop_interval_seconds)
                        continue

                    dashboard_signal_text = f"mm:{plan.half_spread_bps:.2f}bps inv:{plan.inventory_ratio:+.2f}"
                    self._manage_quote_leg("buy", self.bid_orders, plan.bid_price, plan.bid_qty, now)
                    self._manage_quote_leg("sell", self.ask_orders, plan.ask_price, plan.ask_qty, now)

                self._render_dashboard(quote.mid, data_age, dashboard_signal_text)

                logger.info(
                    "Heartbeat",
                    extra={
                        "event": "heartbeat",
                        "symbol": self.settings.symbol,
                        "price": quote.mid,
                        "reason": self.state.blocked_reason,
                        "stream_health": stream_health,
                        "book_mid": self.local_book.mid_price(),
                        "book_spread_bps": self.local_book.spread_bps(),
                        "regime": regime.regime,
                        "volatility_bps": regime.volatility_bps,
                    },
                )
                self._record_loop_latency(loop_start_ns, perf_counter_ns())
                time.sleep(self.runtime_loop_interval_seconds)
        finally:
            self.market.stop()
            report = write_end_of_day_report(
                self.settings.db_path.parent / "reports",
                self.state,
                self.settings.symbol,
                self.market.health_snapshot(),
            )
            logger.info("Bot stopped", extra={"event": "bot_stopped", "reason": "shutdown"})
            logger.info("Report generated", extra={"event": "eod_report", "reason": str(report)})
            self.db.close()
