"""
Phase 1 Load Testing Utility.

Stress tests multi-exchange functionality:
- Quote aggregation latency
- Order submission/failover latency
- Connection resilience
"""

import asyncio
import time
import logging
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone

from btc_hft.adapters import (
    MultiExchangeMarketDataManager,
    MultiExchangeOrderRouter,
    AdapterFactory,
)
from btc_hft.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class LoadTestResult:
    """Result of a load test run."""
    test_name: str
    total_requests: int
    successful: int
    failed: int
    avg_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    success_rate: float

    def __str__(self) -> str:
        return f"""
{self.test_name}:
  Total: {self.total_requests} | Success: {self.successful} | Failed: {self.failed}
  Success Rate: {self.success_rate * 100:.1f}%
  Latency (ms):
    Avg: {self.avg_latency_ms:.2f}
    P95: {self.p95_latency_ms:.2f}
    P99: {self.p99_latency_ms:.2f}
    Min: {self.min_latency_ms:.2f}
    Max: {self.max_latency_ms:.2f}
"""


class Phase1LoadTester:
    """Load tests for Phase 1 multi-exchange functionality."""

    def __init__(self, primary_adapter, fallback_adapters: Optional[list] = None):
        """
        Initialize load tester.
        
        Args:
            primary_adapter: Primary exchange adapter
            fallback_adapters: List of fallback adapters
        """
        self.primary_adapter = primary_adapter
        self.fallback_adapters = fallback_adapters or []
        self.market_mgr = MultiExchangeMarketDataManager(primary_adapter, fallback_adapters)
        self.router = MultiExchangeOrderRouter(primary_adapter, fallback_adapters)
        self.latencies: list[float] = []

    async def run_quote_aggregation_load_test(self, num_requests: int = 1000) -> LoadTestResult:
        """
        Load test quote aggregation.
        
        Args:
            num_requests: Number of quote aggregation requests to make
            
        Returns:
            LoadTestResult with performance metrics
        """
        logger.info(f"Starting quote aggregation load test ({num_requests} requests)")
        
        await self.market_mgr.start()
        self.latencies = []
        
        successful = 0
        failed = 0
        
        try:
            for i in range(num_requests):
                start = time.time()
                try:
                    quote = await self.market_mgr.get_aggregated_quote()
                    if quote:
                        successful += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    logger.debug(f"Request {i} failed: {e}")
                
                elapsed_ms = (time.time() - start) * 1000
                self.latencies.append(elapsed_ms)
                
                if (i + 1) % 100 == 0:
                    logger.info(f"  Completed {i + 1}/{num_requests} requests")
        
        finally:
            await self.market_mgr.stop()
        
        return self._calculate_result(
            "Quote Aggregation",
            num_requests,
            successful,
            failed
        )

    async def run_order_submission_load_test(self, num_requests: int = 100) -> LoadTestResult:
        """
        Load test order submission/routing.
        
        Args:
            num_requests: Number of order submissions to make
            
        Returns:
            LoadTestResult with performance metrics
        """
        logger.info(f"Starting order submission load test ({num_requests} requests)")
        
        self.latencies = []
        successful = 0
        failed = 0
        
        for i in range(num_requests):
            start = time.time()
            try:
                qty = 0.01 + (i % 10) * 0.001  # Vary quantity
                price = 40000 + (i % 100)  # Vary price
                
                result = await self.router.submit_order("BTCUSD", "buy", qty, price)
                if result.success:
                    successful += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                logger.debug(f"Request {i} failed: {e}")
            
            elapsed_ms = (time.time() - start) * 1000
            self.latencies.append(elapsed_ms)
            
            if (i + 1) % 10 == 0:
                logger.info(f"  Completed {i + 1}/{num_requests} requests")
        
        return self._calculate_result(
            "Order Submission",
            num_requests,
            successful,
            failed
        )

    async def run_failover_load_test(self, num_requests: int = 100) -> LoadTestResult:
        """
        Load test failover behavior (requires simulated failures).
        
        Args:
            num_requests: Number of requests to make
            
        Returns:
            LoadTestResult with performance metrics
        """
        logger.info(f"Starting failover load test ({num_requests} requests)")
        
        # This test requires mocking to simulate primary exchange failures
        # For now, it's a placeholder for when running with instrumented adapters
        logger.warning("Failover load test requires mock adapters - skipping")
        
        return LoadTestResult(
            test_name="Failover",
            total_requests=num_requests,
            successful=0,
            failed=0,
            avg_latency_ms=0,
            p95_latency_ms=0,
            p99_latency_ms=0,
            min_latency_ms=0,
            max_latency_ms=0,
            success_rate=0
        )

    def _calculate_result(self, name: str, total: int, successful: int, failed: int) -> LoadTestResult:
        """Calculate performance metrics from latency data."""
        if not self.latencies:
            return LoadTestResult(name, total, successful, failed, 0, 0, 0, 0, 0, 0)
        
        sorted_latencies = sorted(self.latencies)
        avg = sum(self.latencies) / len(self.latencies)
        p95_idx = int(len(sorted_latencies) * 0.95)
        p99_idx = int(len(sorted_latencies) * 0.99)
        
        return LoadTestResult(
            test_name=name,
            total_requests=total,
            successful=successful,
            failed=failed,
            avg_latency_ms=avg,
            p95_latency_ms=sorted_latencies[p95_idx] if p95_idx < len(sorted_latencies) else 0,
            p99_latency_ms=sorted_latencies[p99_idx] if p99_idx < len(sorted_latencies) else 0,
            min_latency_ms=min(self.latencies),
            max_latency_ms=max(self.latencies),
            success_rate=successful / total if total > 0 else 0
        )


async def run_phase1_load_tests(settings: Settings):
    """
    Run full Phase 1 load test suite.
    
    Args:
        settings: Bot configuration
    """
    logger.info("Starting Phase 1 Load Tests")
    
    try:
        # Create adapters
        alpaca = AdapterFactory.create("alpaca", settings=settings)
        coinbase = AdapterFactory.create("coinbase", product_id="BTC-USD")
        
        # Create tester
        tester = Phase1LoadTester(alpaca, [coinbase])
        
        # Run tests
        results = []
        
        # Quote aggregation test
        result = await tester.run_quote_aggregation_load_test(num_requests=100)
        results.append(result)
        logger.info(str(result))
        
        # Order submission test
        result = await tester.run_order_submission_load_test(num_requests=20)
        results.append(result)
        logger.info(str(result))
        
        # Failover test
        result = await tester.run_failover_load_test(num_requests=20)
        results.append(result)
        
        logger.info("Phase 1 Load Tests Complete")
        return results
        
    except Exception as e:
        logger.error(f"Load test failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    
    # For demonstration, would need actual settings
    # results = asyncio.run(run_phase1_load_tests(settings))
    logger.info("Phase 1 Load Testing utility ready. Use via Python API or CLI.")
