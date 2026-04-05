"""Async connection pooling to reduce connection setup latency."""

from dataclasses import dataclass
import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class PooledConnectionStats:
    key: str
    available: int
    in_use: int
    created_total: int


class AsyncConnectionPool:
    """Generic async connection pool keyed by endpoint or venue."""

    def __init__(self, factory: Callable[[str], Any | Awaitable[Any]], max_size_per_key: int = 4):
        self._factory = factory
        self._max_size = max_size_per_key
        self._available: dict[str, list[Any]] = defaultdict(list)
        self._in_use: dict[str, set[int]] = defaultdict(set)
        self._conn_key: dict[int, str] = {}
        self._created_total: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def _create(self, key: str) -> Any:
        conn = self._factory(key)
        if asyncio.iscoroutine(conn):
            conn = await conn
        connect = getattr(conn, "connect", None)
        if callable(connect):
            maybe_awaitable = connect()
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable
        self._created_total[key] += 1
        return conn

    async def acquire(self, key: str) -> Any:
        async with self._lock:
            if self._available[key]:
                conn = self._available[key].pop()
                self._in_use[key].add(id(conn))
                self._conn_key[id(conn)] = key
                return conn

            total_for_key = len(self._available[key]) + len(self._in_use[key])
            if total_for_key >= self._max_size:
                raise RuntimeError(f"Connection pool exhausted for key={key}")

            conn = await self._create(key)
            self._in_use[key].add(id(conn))
            self._conn_key[id(conn)] = key
            return conn

    async def release(self, conn: Any) -> None:
        async with self._lock:
            conn_id = id(conn)
            key = self._conn_key.get(conn_id)
            if key is None:
                return

            self._in_use[key].discard(conn_id)
            self._available[key].append(conn)

    async def warmup(self, key: str, count: int) -> None:
        if count <= 0:
            return
        for _ in range(min(count, self._max_size)):
            conn = await self.acquire(key)
            await self.release(conn)

    async def close(self) -> None:
        async with self._lock:
            for key, conns in self._available.items():
                for conn in conns:
                    close = getattr(conn, "close", None)
                    if callable(close):
                        maybe_awaitable = close()
                        if asyncio.iscoroutine(maybe_awaitable):
                            await maybe_awaitable
            self._available.clear()
            self._in_use.clear()
            self._conn_key.clear()

    async def health(self) -> dict[str, PooledConnectionStats]:
        async with self._lock:
            return {
                key: PooledConnectionStats(
                    key=key,
                    available=len(self._available[key]),
                    in_use=len(self._in_use[key]),
                    created_total=self._created_total[key],
                )
                for key in set(list(self._available.keys()) + list(self._in_use.keys()))
            }
