"""
Circuit breaker for enterprise risk management.

Implements automated kill-switches for:
- Position size thresholds
- Daily loss thresholds
- Consecutive loss limits
- Execution error rates
- Data staleness

Once triggered, circuit stays open until manual reset or recovery conditions met.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitBreakerState(Enum):
    """States of the circuit breaker."""
    CLOSED = "closed"      # Normal operation, accepting orders
    OPEN = "open"          # Tripped, rejecting orders
    HALF_OPEN = "half_open"  # Testing recovery, accept 1 order


class BreakReason(Enum):
    """Reasons the circuit breaker was triggered."""
    POSITION_LIMIT = "position_limit_exceeded"
    DAILY_LOSS_LIMIT = "daily_loss_limit_exceeded"
    CONSECUTIVE_LOSSES = "consecutive_losses_exceeded"
    EXECUTION_ERROR_RATE = "execution_error_rate_too_high"
    DATA_STALE = "market_data_stale"
    MANUAL_TRIGGER = "manual_trigger"


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    max_position_btc: float  # Max position before trigger
    max_daily_loss_usd: float  # Max loss before trigger
    max_consecutive_losses: int  # Max consecutive losses
    max_error_rate: float  # Max execution error rate (0.0-1.0)
    data_stale_seconds: int  # Mark data stale after N seconds
    recovery_time_minutes: int  # Time before attempting recovery
    cooldown_trade_count: int  # Number of trades to cool down


class CircuitBreaker:
    """
    Automated risk kill-switch.
    
    Monitors position, loss, and execution metrics. Trips if any threshold exceeded.
    Once open, rejects all orders until manual reset or recovery conditions met.
    
    Usage:
        breaker = CircuitBreaker(config)
        
        is_open = breaker.should_reject_order(position_qty, daily_loss, etc)
        if is_open:
            logger.warning("Circuit breaker tripped!")
        
        breaker.reset()  # Manual reset
    """

    def __init__(self, config: CircuitBreakerConfig):
        """
        Initialize circuit breaker.
        
        Args:
            config: CircuitBreakerConfig with thresholds
        """
        self.config = config
        self.state = CircuitBreakerState.CLOSED
        self.reason: Optional[BreakReason] = None
        self.triggered_at: Optional[datetime] = None
        
        # Metrics
        self._execution_errors = 0
        self._total_executions = 0
        self._last_successful_trade_at: Optional[datetime] = None
        
        logger.info(
            "CircuitBreaker initialized",
            extra={
                "event": "circuit_breaker_init",
                "max_position": config.max_position_btc,
                "max_daily_loss": config.max_daily_loss_usd,
                "max_consecutive_losses": config.max_consecutive_losses,
                "recovery_minutes": config.recovery_time_minutes,
            }
        )

    def should_reject_order(
        self,
        position_qty: float,
        daily_loss_usd: float,
        consecutive_losses: int,
        data_age_seconds: float,
    ) -> bool:
        """
        Check if order should be rejected due to circuit breaker.
        
        Args:
            position_qty: Current position in BTC
            daily_loss_usd: Current daily loss in USD
            consecutive_losses: Number of consecutive losses
            data_age_seconds: Age of latest market data
            
        Returns:
            True if order should be rejected
        """
        # Check if already open
        if self.state == CircuitBreakerState.OPEN:
            # Check recovery conditions
            if self._should_attempt_recovery():
                logger.info("Attempting circuit breaker recovery (half-open)")
                self.state = CircuitBreakerState.HALF_OPEN
                return False  # Allow 1 test order
            else:
                return True  # Still rejecting

        # Check triggers
        if abs(position_qty) > self.config.max_position_btc:
            self._trip(BreakReason.POSITION_LIMIT)
            return True

        if daily_loss_usd > self.config.max_daily_loss_usd:
            self._trip(BreakReason.DAILY_LOSS_LIMIT)
            return True

        if consecutive_losses > self.config.max_consecutive_losses:
            self._trip(BreakReason.CONSECUTIVE_LOSSES)
            return True

        if data_age_seconds > self.config.data_stale_seconds:
            self._trip(BreakReason.DATA_STALE)
            return True

        error_rate = self._get_error_rate()
        if error_rate > self.config.max_error_rate:
            self._trip(BreakReason.EXECUTION_ERROR_RATE)
            return True

        return False

    def record_execution(self, success: bool) -> None:
        """
        Record execution result for error rate tracking.
        
        Args:
            success: Whether execution succeeded
        """
        self._total_executions += 1
        if not success:
            self._execution_errors += 1

    def record_fill(self) -> None:
        """Record successful fill."""
        self._last_successful_trade_at = datetime.now(timezone.utc)
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.info("Circuit breaker recovery successful (closing)")
            self.state = CircuitBreakerState.CLOSED
            self.reason = None
            self.triggered_at = None

    def _trip(self, reason: BreakReason) -> None:
        """
        Trip the circuit breaker.
        
        Args:
            reason: Reason for trigger
        """
        if self.state == CircuitBreakerState.CLOSED:
            self.state = CircuitBreakerState.OPEN
            self.reason = reason
            self.triggered_at = datetime.now(timezone.utc)
            
            logger.error(
                f"Circuit breaker TRIPPED: {reason.value}",
                extra={
                    "event": "circuit_breaker_tripped",
                    "reason": reason.value,
                    "timestamp": self.triggered_at.isoformat(),
                }
            )

    def _should_attempt_recovery(self) -> bool:
        """Check if recovery conditions are met."""
        if not self.triggered_at:
            return False

        elapsed = (datetime.now(timezone.utc) - self.triggered_at).total_seconds() / 60
        return elapsed >= self.config.recovery_time_minutes

    def _get_error_rate(self) -> float:
        """Get execution error rate."""
        if self._total_executions == 0:
            return 0.0
        return self._execution_errors / self._total_executions

    def reset(self) -> None:
        """
        Manually reset circuit breaker.
        
        Used after investigation determines conditions are safe.
        """
        old_state = self.state
        old_reason = self.reason
        
        self.state = CircuitBreakerState.CLOSED
        self.reason = None
        self.triggered_at = None
        self._execution_errors = 0
        self._total_executions = 0
        
        logger.warning(
            f"Circuit breaker manually reset (was {old_state.value}: {old_reason.value if old_reason else 'unknown'})",
            extra={
                "event": "circuit_breaker_reset",
                "old_reason": old_reason.value if old_reason else None,
            }
        )

    def get_status(self) -> dict:
        """Get current circuit breaker status."""
        return {
            "state": self.state.value,
            "reason": self.reason.value if self.reason else None,
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "error_rate": self._get_error_rate(),
            "total_executions": self._total_executions,
            "last_successful_trade": (
                self._last_successful_trade_at.isoformat() 
                if self._last_successful_trade_at else None
            ),
        }

    def is_open(self) -> bool:
        """Return True if circuit is open (rejecting orders)."""
        return self.state == CircuitBreakerState.OPEN

    def is_half_open(self) -> bool:
        """Return True if circuit is half-open (recovery testing)."""
        return self.state == CircuitBreakerState.HALF_OPEN

    def is_closed(self) -> bool:
        """Return True if circuit is closed (accepting orders)."""
        return self.state == CircuitBreakerState.CLOSED
