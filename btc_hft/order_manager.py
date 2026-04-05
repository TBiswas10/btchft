from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from .alpaca_clients import ManagedOrder, TradingService
from .models import QuoteSnapshot

logger = logging.getLogger(__name__)


@dataclass
class FillResult:
    side: str
    qty: float
    price: float
    order_id: str
    client_order_id: str
    limit_price: float
    status: str
    is_partial: bool = False


class OrderManager:
    def __init__(self, trading: TradingService | None, dry_run: bool = False) -> None:
        self.trading = trading
        self.dry_run = dry_run
        self.pending: ManagedOrder | None = None

    def submit(self, side: str, qty: float, limit_price: float) -> ManagedOrder | None:
        client_order_id = f"btc-hft-{uuid.uuid4().hex[:20]}"
        if self.dry_run:
            order_id = f"dry-{uuid.uuid4().hex[:16]}"
        else:
            if self.trading is None:
                raise RuntimeError("Trading service is required when DRY_RUN is false.")
            try:
                order = self.trading.submit_limit_order(
                    side=side,
                    qty_btc=qty,
                    limit_price=limit_price,
                    client_order_id=client_order_id,
                )
                order_id = str(order.id)
            except Exception as exc:
                logger.warning(
                    "Order submission rejected",
                    extra={"event": "order_rejected", "side": side, "qty": qty, "price": limit_price, "reason": str(exc)},
                )
                self.pending = None
                return None

        managed = ManagedOrder(
            order_id=order_id,
            client_order_id=client_order_id,
            side=side,
            qty=float(qty),
            limit_price=float(limit_price),
            submitted_at=datetime.now(timezone.utc),
        )
        self.pending = managed
        return managed

    def reconcile(self, current_quote: QuoteSnapshot | None = None, expected_fill_prob: float | None = None) -> list[FillResult]:
        if not self.pending:
            return []

        if self.dry_run:
            side = self.pending.side
            limit_price = self.pending.limit_price
            if current_quote is not None and current_quote.bid > 0 and current_quote.ask > 0:
                marketable = (
                    side == "buy" and current_quote.ask <= limit_price
                ) or (
                    side == "sell" and current_quote.bid >= limit_price
                )
                if not marketable:
                    # Passive fill model: allow occasional maker fills without explicit cross in dry-run/replay.
                    prob = max(0.0, min(float(expected_fill_prob or 0.0), 1.0))
                    if prob <= 0.0 or random.random() > prob * 0.08:
                        return []
                    base_fill_price = limit_price
                else:
                    if side == "buy":
                        base_fill_price = min(limit_price, current_quote.ask)
                    else:
                        base_fill_price = max(limit_price, current_quote.bid)
            else:
                base_fill_price = limit_price

            slippage_bps = max(0.0, random.gauss(mu=0.5, sigma=0.3))
            if side == "buy":
                fill_price = base_fill_price * (1 + slippage_bps / 10000.0)
            else:
                fill_price = base_fill_price * (1 - slippage_bps / 10000.0)
            fill = FillResult(
                side=self.pending.side,
                qty=self.pending.qty,
                price=fill_price,
                order_id=self.pending.order_id,
                client_order_id=self.pending.client_order_id,
                limit_price=self.pending.limit_price,
                status="filled",
                is_partial=False,
            )
            self.pending = None
            return [fill]

        if self.trading is None:
            return []

        order = self.trading.get_order(self.pending.order_id)
        status = str(order.status).lower()
        current_filled_qty = float(getattr(order, "filled_qty", 0.0) or 0.0)
        fills: list[FillResult] = []

        if current_filled_qty > self.pending.filled_qty:
            delta = current_filled_qty - self.pending.filled_qty
            fills.append(
                FillResult(
                    side=self.pending.side,
                    qty=delta,
                    price=float(getattr(order, "filled_avg_price", None) or self.pending.limit_price),
                    order_id=self.pending.order_id,
                    client_order_id=self.pending.client_order_id,
                    limit_price=self.pending.limit_price,
                    status=status,
                    is_partial=current_filled_qty < self.pending.qty or "partial" in status,
                )
            )
            self.pending.filled_qty = current_filled_qty
            self.pending.last_status = status

        if "filled" in status and self.pending.filled_qty >= self.pending.qty:
            if not fills:
                fills.append(
                    FillResult(
                        side=self.pending.side,
                        qty=float(getattr(order, "filled_qty", self.pending.qty)),
                        price=float(getattr(order, "filled_avg_price", None) or self.pending.limit_price),
                        order_id=self.pending.order_id,
                        client_order_id=self.pending.client_order_id,
                        limit_price=self.pending.limit_price,
                        status=status,
                        is_partial=False,
                    )
                )
            self.pending = None
            return fills

        if self.trading.is_final_status(order.status):
            self.pending = None
            return fills

        return fills

    def cancel_pending(self) -> None:
        if self.pending:
            if not self.dry_run and self.trading is not None:
                try:
                    self.trading.cancel_order(self.pending.order_id)
                except Exception as exc:
                    logger.warning(
                        "Cancel ignored",
                        extra={
                            "event": "cancel_ignored",
                            "order_id": self.pending.order_id,
                            "reason": str(exc),
                        },
                    )
            self.pending = None

    def has_pending(self) -> bool:
        return self.pending is not None

    def pending_age_seconds(self, now: datetime) -> float:
        if not self.pending:
            return 0.0
        return (now - self.pending.submitted_at).total_seconds()

    def remaining_qty(self) -> float:
        if not self.pending:
            return 0.0
        return max(self.pending.qty - self.pending.filled_qty, 0.0)

    def replace_pending(self, limit_price: float) -> ManagedOrder | None:
        if not self.pending:
            return None
        side = self.pending.side
        remaining_qty = self.remaining_qty()
        self.cancel_pending()
        if remaining_qty <= 0:
            return None
        return self.submit(side, remaining_qty, limit_price)
