from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from btc_hft.order_manager import OrderManager


@dataclass
class FakeOrder:
    id: str
    status: str
    filled_qty: float = 0.0
    filled_avg_price: float = 0.0


class FakeTradingService:
    def __init__(self) -> None:
        self.orders: dict[str, FakeOrder] = {}
        self.submitted = []
        self.canceled = []

    def submit_limit_order(self, side: str, qty_btc: float, limit_price: float, client_order_id: str):
        order = FakeOrder(id="order-1", status="new", filled_qty=0.0, filled_avg_price=0.0)
        self.orders[order.id] = order
        self.submitted.append((side, qty_btc, limit_price, client_order_id))
        return SimpleNamespace(id=order.id)

    def get_order(self, order_id: str):
        return self.orders[order_id]

    def cancel_order(self, order_id: str) -> None:
        self.canceled.append(order_id)
        self.orders[order_id].status = "canceled"

    @staticmethod
    def is_final_status(status: str) -> bool:
        return str(status).lower() in {"filled", "canceled", "expired", "rejected"}


class RejectingTradingService(FakeTradingService):
    def submit_limit_order(self, side: str, qty_btc: float, limit_price: float, client_order_id: str):
        raise RuntimeError("insufficient balance")


def test_partial_fill_reconcile_and_replace():
    trading = FakeTradingService()
    manager = OrderManager(trading, dry_run=False)
    pending = manager.submit("buy", 1.0, 100.0)
    trading.orders[pending.order_id] = FakeOrder(id=pending.order_id, status="partially_filled", filled_qty=0.4, filled_avg_price=101.0)

    fills = manager.reconcile()
    assert len(fills) == 1
    assert fills[0].qty == 0.4
    assert fills[0].is_partial is True
    assert manager.remaining_qty() == 0.6

    trading.orders[pending.order_id] = FakeOrder(id=pending.order_id, status="filled", filled_qty=1.0, filled_avg_price=100.5)
    fills = manager.reconcile()
    assert len(fills) == 1
    assert fills[0].qty == 0.6
    assert manager.has_pending() is False


def test_replace_pending_uses_remaining_qty():
    trading = FakeTradingService()
    manager = OrderManager(trading, dry_run=False)
    pending = manager.submit("sell", 2.0, 105.0)
    manager.pending.filled_qty = 1.25

    replaced = manager.replace_pending(104.0)
    assert replaced is not None
    assert replaced.qty == 0.75
    assert trading.canceled == [pending.order_id]


def test_rejected_submit_returns_none():
    manager = OrderManager(RejectingTradingService(), dry_run=False)
    order = manager.submit("buy", 1.0, 100.0)
    assert order is None
    assert manager.has_pending() is False
