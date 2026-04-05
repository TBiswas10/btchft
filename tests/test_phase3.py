"""Tests for Phase 3 latency optimization infrastructure."""

from dataclasses import dataclass

import pytest

from btc_hft.adapters import AdapterFactory, FixAdapter
from btc_hft.latency import (
    AsyncConnectionPool,
    GrpcOrderGateway,
    InMemoryOrderTransport,
    LocalOrderBookEngine,
)


class TestLocalOrderBookEngine:
    def test_snapshot_and_top_of_book(self):
        book = LocalOrderBookEngine("BTCUSD")
        book.apply_snapshot(
            bids=[(40000.0, 1.2), (39999.5, 2.0)],
            asks=[(40001.0, 1.1), (40002.0, 3.5)],
        )

        bid = book.best_bid()
        ask = book.best_ask()

        assert bid is not None
        assert ask is not None
        assert bid.price == 40000.0
        assert ask.price == 40001.0

    def test_delta_update_and_delete(self):
        book = LocalOrderBookEngine("BTCUSD")
        book.apply_snapshot(bids=[(40000.0, 1.0)], asks=[(40001.0, 1.0)])

        book.apply_delta("bid", 40000.5, 2.0)
        assert book.best_bid() is not None
        assert book.best_bid().price == 40000.5

        book.apply_delta("bid", 40000.5, 0.0)
        assert book.best_bid() is not None
        assert book.best_bid().price == 40000.0

    def test_mid_and_spread_bps(self):
        book = LocalOrderBookEngine("BTCUSD")
        book.apply_snapshot(bids=[(100.0, 1.0)], asks=[(101.0, 1.0)])

        assert book.mid_price() == 100.5
        assert book.spread_bps() == pytest.approx((1.0 / 100.5) * 10000.0)

    def test_depth_notional(self):
        book = LocalOrderBookEngine("BTCUSD")
        book.apply_snapshot(
            bids=[(100.0, 1.0), (99.0, 2.0), (98.0, 3.0)],
            asks=[(101.0, 1.0), (102.0, 2.0), (103.0, 3.0)],
        )

        bid_notional_2 = book.depth_notional("bid", levels=2)
        ask_notional_2 = book.depth_notional("ask", levels=2)

        assert bid_notional_2 == pytest.approx(100.0 * 1.0 + 99.0 * 2.0)
        assert ask_notional_2 == pytest.approx(101.0 * 1.0 + 102.0 * 2.0)


@dataclass
class _MockConn:
    key: str
    connected: bool = False
    closed: bool = False

    async def connect(self):
        self.connected = True

    async def close(self):
        self.closed = True


class TestAsyncConnectionPool:
    @pytest.mark.asyncio
    async def test_acquire_release_reuse(self):
        async def factory(key: str):
            return _MockConn(key=key)

        pool = AsyncConnectionPool(factory=factory, max_size_per_key=2)
        conn1 = await pool.acquire("coinbase")
        assert conn1.connected

        await pool.release(conn1)
        conn2 = await pool.acquire("coinbase")

        assert id(conn1) == id(conn2)

    @pytest.mark.asyncio
    async def test_pool_exhaustion(self):
        async def factory(key: str):
            return _MockConn(key=key)

        pool = AsyncConnectionPool(factory=factory, max_size_per_key=1)
        _ = await pool.acquire("kraken")

        with pytest.raises(RuntimeError):
            await pool.acquire("kraken")

    @pytest.mark.asyncio
    async def test_health_and_close(self):
        async def factory(key: str):
            return _MockConn(key=key)

        pool = AsyncConnectionPool(factory=factory, max_size_per_key=2)
        conn = await pool.acquire("coinbase")
        await pool.release(conn)

        health = await pool.health()
        assert "coinbase" in health
        assert health["coinbase"].available == 1

        await pool.close()
        health_after = await pool.health()
        assert health_after == {}


class TestGrpcOrderGateway:
    @pytest.mark.asyncio
    async def test_submit_and_cancel(self):
        gateway = GrpcOrderGateway(transport=InMemoryOrderTransport())

        ack = await gateway.submit_limit_order("BTCUSD", "buy", 0.1, 40000.0)
        assert ack.accepted
        assert ack.order_id is not None

        canceled = await gateway.cancel_order(ack.order_id)
        assert canceled

    @pytest.mark.asyncio
    async def test_rejection_path(self):
        gateway = GrpcOrderGateway(transport=InMemoryOrderTransport())
        ack = await gateway.submit_limit_order("BTCUSD", "buy", 0.0, 40000.0)

        assert not ack.accepted
        assert ack.reject_reason == "invalid_qty_or_price"

    @pytest.mark.asyncio
    async def test_latency_summary(self):
        gateway = GrpcOrderGateway(transport=InMemoryOrderTransport())

        for _ in range(5):
            await gateway.submit_limit_order("BTCUSD", "buy", 0.1, 40000.0)

        summary = gateway.latency_summary()
        assert summary["count"] == 5.0
        assert summary["median_us"] >= 0.0
        assert summary["p95_us"] >= 0.0
        assert summary["max_us"] >= 0.0


class TestFixAdapterAndFactory:
    @pytest.mark.asyncio
    async def test_fix_adapter_lifecycle_and_quote(self):
        adapter = FixAdapter(venue="coinbase", symbol="BTCUSD", paper_mode=True)
        assert not adapter.is_connected

        await adapter.start()
        assert adapter.is_connected

        await adapter.subscribe_quotes(["BTCUSD"])
        quote = await adapter.get_quote("BTCUSD")
        assert quote is not None
        assert quote.exchange == "coinbase-FIX"

        await adapter.stop()
        assert not adapter.is_connected

    @pytest.mark.asyncio
    async def test_fix_submit_and_status(self):
        adapter = FixAdapter(venue="kraken", symbol="BTCUSD", paper_mode=True)
        await adapter.start()

        order_id = await adapter.submit_order("BTCUSD", "buy", 0.1, 40000.0)
        assert order_id is not None

        status = await adapter.get_order_status(order_id)
        assert status is not None
        assert status.status == "pending"

        canceled = await adapter.cancel_order(order_id)
        assert canceled

        status_after = await adapter.get_order_status(order_id)
        assert status_after is not None
        assert status_after.status == "canceled"

    def test_factory_supports_fix(self):
        adapter = AdapterFactory.create("fix", venue="coinbase", symbol="BTCUSD")
        assert isinstance(adapter, FixAdapter)

        supported = AdapterFactory.list_supported()
        assert "fix" in supported
        assert "coinbase_fix" in supported
        assert "kraken_fix" in supported
