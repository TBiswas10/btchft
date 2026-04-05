"""gRPC-ready order gateway abstraction with latency telemetry."""

from dataclasses import dataclass
from time import perf_counter_ns
from statistics import median
from typing import Protocol


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    qty: float
    price: float


@dataclass(frozen=True)
class OrderAck:
    accepted: bool
    order_id: str | None = None
    reject_reason: str | None = None


class OrderTransport(Protocol):
    async def submit(self, request: OrderRequest) -> OrderAck:
        pass

    async def cancel(self, order_id: str) -> bool:
        pass


class InMemoryOrderTransport:
    """Simple transport used by tests and local dry-runs."""

    def __init__(self):
        self._orders: dict[str, OrderRequest] = {}
        self._next = 1

    async def submit(self, request: OrderRequest) -> OrderAck:
        if request.qty <= 0 or request.price <= 0:
            return OrderAck(accepted=False, reject_reason="invalid_qty_or_price")

        order_id = f"grpc-{self._next}"
        self._next += 1
        self._orders[order_id] = request
        return OrderAck(accepted=True, order_id=order_id)

    async def cancel(self, order_id: str) -> bool:
        return self._orders.pop(order_id, None) is not None


class GrpcOrderGateway:
    """Transport-neutral order gateway compatible with future grpcio services."""

    def __init__(self, transport: OrderTransport):
        self._transport = transport
        self._submit_latencies_us: list[float] = []

    async def submit_limit_order(self, symbol: str, side: str, qty: float, price: float) -> OrderAck:
        request = OrderRequest(symbol=symbol, side=side, qty=qty, price=price)
        start = perf_counter_ns()
        ack = await self._transport.submit(request)
        end = perf_counter_ns()
        self._submit_latencies_us.append((end - start) / 1000.0)
        return ack

    async def cancel_order(self, order_id: str) -> bool:
        return await self._transport.cancel(order_id)

    def latency_summary(self) -> dict[str, float]:
        if not self._submit_latencies_us:
            return {
                "count": 0,
                "median_us": 0.0,
                "p95_us": 0.0,
                "max_us": 0.0,
            }

        values = sorted(self._submit_latencies_us)
        p95_idx = int(0.95 * (len(values) - 1))
        return {
            "count": float(len(values)),
            "median_us": float(median(values)),
            "p95_us": float(values[p95_idx]),
            "max_us": float(values[-1]),
        }
