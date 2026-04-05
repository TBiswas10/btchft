"""
Tests for Phase 2: Enterprise Risk & Compliance.

Validates:
- AuditLogger functionality
- CircuitBreaker logic
- ComplianceExporter formats
"""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile

from btc_hft.audit_logger import (
    AuditLogger,
    AuditEvent,
    EventType,
    EventSeverity,
)
from btc_hft.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
    BreakReason,
)
from btc_hft.compliance import (
    ComplianceExporter,
    TradeRecord,
)


# ============================================================================
# AuditLogger Tests
# ============================================================================

class TestAuditLogger:
    """Test audit logging functionality."""

    def test_audit_logger_initialization_sqlite(self):
        """AuditLogger can be initialized with SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            logger = AuditLogger(db_path=db_path)
            
            assert logger.backend == "sqlite"
            assert logger.db_path == db_path

    def test_audit_event_creation(self):
        """AuditEvent can be created with various fields."""
        event = AuditEvent(
            event_id="evt-001",
            timestamp=datetime.now(timezone.utc),
            event_type=EventType.ORDER_SUBMITTED,
            severity=EventSeverity.INFO,
            exchange="Alpaca",
            symbol="BTC/USD",
            order_id="order-123",
            side="buy",
            qty=0.1,
            price=40000.0,
        )
        
        assert event.event_id == "evt-001"
        assert event.event_type == EventType.ORDER_SUBMITTED
        assert event.severity == EventSeverity.INFO

    def test_audit_event_to_dict(self):
        """AuditEvent converts to dictionary with proper types."""
        now = datetime.now(timezone.utc)
        event = AuditEvent(
            event_id="evt-001",
            timestamp=now,
            event_type=EventType.FULL_FILL,
            severity=EventSeverity.INFO,
            exchange="Alpaca",
            symbol="BTC/USD",
            fill_qty=0.1,
            fill_price=40000.0,
        )
        
        d = event.to_dict()
        assert d['event_type'] == "full_fill"
        assert d['severity'] == "info"
        assert d['timestamp'] == now.isoformat()

    def test_audit_logger_logs_event(self):
        """AuditLogger stores events in memory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            logger = AuditLogger(db_path=db_path)
            
            event = AuditEvent(
                event_id="evt-001",
                timestamp=datetime.now(timezone.utc),
                event_type=EventType.ORDER_SUBMITTED,
                severity=EventSeverity.INFO,
                exchange="Alpaca",
                symbol="BTC/USD",
            )
            
            logger.log_event(event)
            
            assert len(logger.events) == 1
            assert logger.events[0].event_id == "evt-001"

    def test_audit_logger_get_events_by_type(self):
        """AuditLogger filters events by type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            logger = AuditLogger(db_path=db_path)
            
            now = datetime.now(timezone.utc)
            
            # Log multiple event types
            for i in range(3):
                logger.log_event(AuditEvent(
                    event_id=f"submitted-{i}",
                    timestamp=now + timedelta(seconds=i),
                    event_type=EventType.ORDER_SUBMITTED,
                    severity=EventSeverity.INFO,
                    exchange="Alpaca",
                    symbol="BTC/USD",
                ))
            
            for i in range(2):
                logger.log_event(AuditEvent(
                    event_id=f"fill-{i}",
                    timestamp=now + timedelta(seconds=i+10),
                    event_type=EventType.FULL_FILL,
                    severity=EventSeverity.INFO,
                    exchange="Alpaca",
                    symbol="BTC/USD",
                ))
            
            # Filter by type
            submitted = logger.get_events_by_type(EventType.ORDER_SUBMITTED)
            assert len(submitted) == 3
            
            fills = logger.get_events_by_type(EventType.FULL_FILL)
            assert len(fills) == 2

    def test_audit_logger_get_risk_events(self):
        """AuditLogger filters risk and warning events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            logger = AuditLogger(db_path=db_path)
            
            now = datetime.now(timezone.utc)
            
            # Log normal events
            logger.log_event(AuditEvent(
                event_id="evt-1",
                timestamp=now,
                event_type=EventType.ORDER_SUBMITTED,
                severity=EventSeverity.INFO,
                exchange="Alpaca",
                symbol="BTC/USD",
            ))
            
            # Log risk events
            logger.log_event(AuditEvent(
                event_id="evt-2",
                timestamp=now + timedelta(seconds=1),
                event_type=EventType.RISK_BLOCK,
                severity=EventSeverity.WARNING,
                exchange="Alpaca",
                symbol="BTC/USD",
                risk_reason="position_limit_exceeded",
            ))
            
            logger.log_event(AuditEvent(
                event_id="evt-3",
                timestamp=now + timedelta(seconds=2),
                event_type=EventType.CIRCUIT_BREAKER_TRIGGERED,
                severity=EventSeverity.ERROR,
                exchange="Alpaca",
                symbol="BTC/USD",
            ))
            
            risk_events = logger.get_risk_events()
            assert len(risk_events) == 2
            assert all(e.event_type in {EventType.RISK_BLOCK, EventType.CIRCUIT_BREAKER_TRIGGERED} for e in risk_events)

    def test_audit_logger_summary(self):
        """AuditLogger provides summary statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            logger = AuditLogger(db_path=db_path)
            
            now = datetime.now(timezone.utc)
            
            logger.log_event(AuditEvent(
                event_id="evt-1",
                timestamp=now,
                event_type=EventType.ORDER_SUBMITTED,
                severity=EventSeverity.INFO,
                exchange="Alpaca",
                symbol="BTC/USD",
            ))
            
            summary = logger.get_summary()
            assert summary["total_events"] == 1
            assert summary["event_type_counts"]["order_submitted"] == 1


# ============================================================================
# CircuitBreaker Tests
# ============================================================================

class TestCircuitBreaker:
    """Test circuit breaker functionality."""

    @pytest.fixture
    def config(self):
        """Provide a test CircuitBreakerConfig."""
        return CircuitBreakerConfig(
            max_position_btc=2.0,
            max_daily_loss_usd=500.0,
            max_consecutive_losses=5,
            max_error_rate=0.1,
            data_stale_seconds=10,
            recovery_time_minutes=5,
            cooldown_trade_count=10,
        )

    def test_circuit_breaker_initialization(self, config):
        """CircuitBreaker initializes in CLOSED state."""
        breaker = CircuitBreaker(config)
        
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.reason is None
        assert breaker.is_closed()
        assert not breaker.is_open()

    def test_circuit_breaker_position_limit_trigger(self, config):
        """CircuitBreaker trips on position limit exceeded."""
        breaker = CircuitBreaker(config)
        
        # Position within limit
        assert not breaker.should_reject_order(
            position_qty=1.5,
            daily_loss_usd=0,
            consecutive_losses=0,
            data_age_seconds=1,
        )
        
        # Position exceeds limit
        assert breaker.should_reject_order(
            position_qty=2.5,
            daily_loss_usd=0,
            consecutive_losses=0,
            data_age_seconds=1,
        )
        
        assert breaker.is_open()
        assert breaker.reason == BreakReason.POSITION_LIMIT

    def test_circuit_breaker_daily_loss_trigger(self, config):
        """CircuitBreaker trips on daily loss limit exceeded."""
        breaker = CircuitBreaker(config)
        
        assert breaker.should_reject_order(
            position_qty=0,
            daily_loss_usd=600.0,  # Exceeds 500.0 limit
            consecutive_losses=0,
            data_age_seconds=1,
        )
        
        assert breaker.is_open()
        assert breaker.reason == BreakReason.DAILY_LOSS_LIMIT

    def test_circuit_breaker_consecutive_losses_trigger(self, config):
        """CircuitBreaker trips on consecutive losses limit."""
        breaker = CircuitBreaker(config)
        
        assert breaker.should_reject_order(
            position_qty=0,
            daily_loss_usd=0,
            consecutive_losses=6,  # Exceeds 5 limit
            data_age_seconds=1,
        )
        
        assert breaker.is_open()
        assert breaker.reason == BreakReason.CONSECUTIVE_LOSSES

    def test_circuit_breaker_data_stale_trigger(self, config):
        """CircuitBreaker trips on stale data."""
        breaker = CircuitBreaker(config)
        
        assert breaker.should_reject_order(
            position_qty=0,
            daily_loss_usd=0,
            consecutive_losses=0,
            data_age_seconds=15,  # Exceeds 10 second limit
        )
        
        assert breaker.is_open()
        assert breaker.reason == BreakReason.DATA_STALE

    def test_circuit_breaker_rejection_when_open(self, config):
        """CircuitBreaker rejects all orders when OPEN."""
        breaker = CircuitBreaker(config)
        
        # Trip the breaker
        breaker.should_reject_order(2.5, 0, 0, 1)
        assert breaker.is_open()
        
        # All subsequent orders rejected
        assert breaker.should_reject_order(0, 0, 0, 1)
        assert breaker.should_reject_order(0, 0, 0, 1)

    def test_circuit_breaker_manual_reset(self, config):
        """CircuitBreaker can be manually reset."""
        breaker = CircuitBreaker(config)
        
        # Trip the breaker
        breaker.should_reject_order(2.5, 0, 0, 1)
        assert breaker.is_open()
        
        # Manual reset
        breaker.reset()
        assert breaker.is_closed()
        assert breaker.reason is None

    def test_circuit_breaker_error_rate_tracking(self, config):
        """CircuitBreaker tracks execution error rate."""
        breaker = CircuitBreaker(config)
        
        # Record some executions
        for _ in range(5):
            breaker.record_execution(success=True)
        
        for _ in range(1):
            breaker.record_execution(success=False)
        
        error_rate = breaker._get_error_rate()
        assert error_rate == pytest.approx(1/6, rel=0.01)

    def test_circuit_breaker_status(self, config):
        """CircuitBreaker provides status information."""
        breaker = CircuitBreaker(config)
        
        status = breaker.get_status()
        assert status['state'] == "closed"
        assert status['reason'] is None
        assert status['error_rate'] == 0.0


# ============================================================================
# ComplianceExporter Tests
# ============================================================================

class TestComplianceExporter:
    """Test compliance export functionality."""

    @pytest.fixture
    def exporter(self):
        """Provide a ComplianceExporter."""
        return ComplianceExporter(firm_name="Test Firm", account_id="TEST-001")

    @pytest.fixture
    def sample_trades(self):
        """Provide sample trade records."""
        now = datetime.now(timezone.utc)
        return [
            TradeRecord(
                execution_id="exec-001",
                execution_time=now,
                symbol="BTC/USD",
                side="BUY",
                quantity=0.1,
                price=40000.0,
                order_id="order-001",
                account_id="TEST-001",
                broker="Alpaca",
                clearing_firm="APEX",
                execution_type="AUTO",
                liquidity_indicator="A",
                fees_paid=0.004,
            ),
            TradeRecord(
                execution_id="exec-002",
                execution_time=now + timedelta(seconds=10),
                symbol="BTC/USD",
                side="SELL",
                quantity=0.1,
                price=40100.0,
                order_id="order-002",
                account_id="TEST-001",
                broker="Alpaca",
                clearing_firm="APEX",
                execution_type="AUTO",
                liquidity_indicator="R",
                fees_paid=0.004,
                gross_pnl=10.0,  # $10 profit
            ),
        ]

    def test_exporter_sec_ats_format(self, exporter, sample_trades):
        """ComplianceExporter generates SEC ATS format."""
        output = exporter.export_sec_ats_format(sample_trades)
        
        assert "SEC" not in output or "Execution Report" in output
        assert "BTC/USD" in output
        assert "40000.0" in output
        assert "BUY|" in output or "BUY" in output
        assert len(output) > 0

    def test_exporter_finra_format(self, exporter, sample_trades):
        """ComplianceExporter generates FINRA format."""
        output = exporter.export_finra_audit_trail(sample_trades)
        
        assert "FINRA" in output
        assert "EXECUTION" in output
        assert "BTC/USD" in output
        assert "GTC" in output  # Good-till-cancel

    def test_exporter_reconciliation_report(self, exporter, sample_trades):
        """ComplianceExporter generates reconciliation report."""
        output = exporter.export_trade_reconciliation(sample_trades)
        
        assert "Reconciliation" in output
        assert "BUY" in output
        assert "SELL" in output
        assert "Total Quantity" in output
        assert "0.2" in output  # 0.1 + 0.1

    def test_exporter_summary_report(self, exporter, sample_trades):
        """ComplianceExporter generates summary report."""
        output = exporter.export_summary_report(sample_trades)
        
        assert "SUMMARY" in output
        assert "Total Executions" in output
        assert "COMPLIANCE STATUS" in output

    def test_exporter_file_export(self, exporter, sample_trades):
        """ComplianceExporter can write to files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "finra_trail.csv"
            
            exporter.export_finra_audit_trail(sample_trades, output_path=output_path)
            
            assert output_path.exists()
            content = output_path.read_text()
            assert "FINRA" in content


# ============================================================================
# Integration Tests
# ============================================================================

class TestPhase2Integration:
    """Integration tests for Phase 2 compliance suite."""

    def test_audit_and_circuit_breaker_together(self):
        """AuditLogger and CircuitBreaker work together."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            logger = AuditLogger(db_path=db_path)
            
            config = CircuitBreakerConfig(
                max_position_btc=1.0,
                max_daily_loss_usd=1000.0,
                max_consecutive_losses=5,
                max_error_rate=0.1,
                data_stale_seconds=10,
                recovery_time_minutes=5,
                cooldown_trade_count=10,
            )
            breaker = CircuitBreaker(config)
            
            now = datetime.now(timezone.utc)
            
            # Position exceeds limit
            breaker.should_reject_order(1.5, 0, 0, 1)
            
            # Log circuit breaker trigger
            logger.log_event(AuditEvent(
                event_id="evt-1",
                timestamp=now,
                event_type=EventType.CIRCUIT_BREAKER_TRIGGERED,
                severity=EventSeverity.ERROR,
                exchange="Alpaca",
                symbol="BTC/USD",
                position_qty=1.5,
                risk_reason="position_limit_exceeded",
            ))
            
            assert breaker.is_open()
            assert len(logger.events) == 1
            assert logger.events[0].event_type == EventType.CIRCUIT_BREAKER_TRIGGERED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
