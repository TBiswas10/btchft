"""
Audit logger for enterprise risk & compliance.

Logs all trades, fills, rejections, and risk events to persistent storage.
Supports:
- SQLite (development/testing)
- PostgreSQL (production)

All events are timestamped, immutable (append-only), and exportable to FINRA format.
"""

import logging
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Types of audit events."""
    # Order events
    ORDER_SUBMITTED = "order_submitted"
    ORDER_ACCEPTED = "order_accepted"
    ORDER_REJECTED = "order_rejected"
    ORDER_CANCELED = "order_canceled"
    ORDER_REPLACED = "order_replaced"
    
    # Fill events
    PARTIAL_FILL = "partial_fill"
    FULL_FILL = "full_fill"
    FILL_RECONCILED = "fill_reconciled"
    
    # Risk events
    RISK_BLOCK = "risk_block"
    CIRCUIT_BREAKER_TRIGGERED = "circuit_breaker_triggered"
    CIRCUIT_BREAKER_RESET = "circuit_breaker_reset"
    POSITION_LIMIT_BREACHED = "position_limit_breached"
    DAILY_LOSS_LIMIT_BREACHED = "daily_loss_limit_breached"
    
    # Session events
    SESSION_START = "session_start"
    SESSION_STOP = "session_stop"
    SESSION_ERROR = "session_error"


class EventSeverity(Enum):
    """Severity levels for audit events."""
    INFO = "info"          # Normal operation (fills, order submitted)
    WARNING = "warning"    # Risk management action (risk block, circuit breaker)
    ERROR = "error"        # System error or compliance violation


@dataclass
class AuditEvent:
    """A single audit event."""
    event_id: str                          # Unique event ID (UUIDv4)
    timestamp: datetime                    # UTC timestamp (immutable)
    event_type: EventType                  # Type of event
    severity: EventSeverity                # Severity level
    exchange: str                          # Exchange name
    symbol: str                            # Trading pair
    
    # Order context
    order_id: Optional[str] = None         # Alpaca/exchange order ID
    side: Optional[str] = None             # "buy" or "sell"
    qty: Optional[float] = None            # Order quantity
    price: Optional[float] = None          # Order price
    
    # Fill context
    fill_qty: Optional[float] = None       # Filled quantity
    fill_price: Optional[float] = None     # Fill price
    cumulative_qty: Optional[float] = None # Cumulative filled
    
    # Risk context
    position_qty: Optional[float] = None   # Position at time of event
    risk_reason: Optional[str] = None      # Why risk block triggered
    daily_loss_usd: Optional[float] = None # Daily loss at time of event
    
    # Metadata
    metadata: dict = field(default_factory=dict)  # Custom key-value pairs
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        d = asdict(self)
        d['event_type'] = self.event_type.value
        d['severity'] = self.severity.value
        d['timestamp'] = self.timestamp.isoformat()
        return d


class AuditLogger:
    """
    Enterprise audit logging system.
    
    Features:
    - Append-only event log (immutable history)
    - Timestamped with UTC precision
    - Supports SQLite (dev) and PostgreSQL (prod)
    - Exportable to FINRA audit trail format
    - Indexed for fast compliance queries
    """

    def __init__(self, db_path: Optional[Path] = None, postgres_url: Optional[str] = None):
        """
        Initialize audit logger.
        
        Args:
            db_path: Path to SQLite database (if using SQLite)
            postgres_url: PostgreSQL connection string (if using PostgreSQL)
                         Format: postgresql://user:password@host:port/dbname
        """
        self.db_path = db_path
        self.postgres_url = postgres_url
        self.events: list[AuditEvent] = []  # In-memory cache
        
        if postgres_url:
            self.backend = "postgresql"
            logger.info("Using PostgreSQL backend for audit logging")
            self._init_postgres()
        else:
            self.backend = "sqlite"
            logger.info(f"Using SQLite backend for audit logging: {db_path}")
            self._init_sqlite()

    def _init_sqlite(self):
        """Initialize SQLite audit table."""
        if not self.db_path:
            return
        
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    order_id TEXT,
                    side TEXT,
                    qty REAL,
                    price REAL,
                    fill_qty REAL,
                    fill_price REAL,
                    cumulative_qty REAL,
                    position_qty REAL,
                    risk_reason TEXT,
                    daily_loss_usd REAL,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp 
                ON audit_events(timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_event_type 
                ON audit_events(event_type)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_symbol 
                ON audit_events(symbol)
            """)
            
            conn.commit()
            conn.close()
            logger.info("SQLite audit table initialized")
        except Exception as e:
            logger.error(f"Failed to initialize SQLite audit table: {e}")

    def _init_postgres(self):
        """Initialize PostgreSQL audit table."""
        try:
            import psycopg2
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    order_id TEXT,
                    side TEXT,
                    qty REAL,
                    price REAL,
                    fill_qty REAL,
                    fill_price REAL,
                    cumulative_qty REAL,
                    position_qty REAL,
                    risk_reason TEXT,
                    daily_loss_usd REAL,
                    metadata JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp 
                ON audit_events(timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_event_type 
                ON audit_events(event_type)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_symbol 
                ON audit_events(symbol)
            """)
            
            conn.commit()
            conn.close()
            logger.info("PostgreSQL audit table initialized")
        except Exception as e:
            logger.warning(f"PostgreSQL not available: {e}. Audit events will be cached in memory.")

    def log_event(self, event: AuditEvent) -> None:
        """
        Log an audit event.
        
        Args:
            event: AuditEvent to log
        """
        self.events.append(event)
        
        # Persist to database
        if self.backend == "sqlite" and self.db_path:
            self._persist_sqlite(event)
        elif self.backend == "postgresql":
            self._persist_postgres(event)
        
        logger.debug(f"Event logged: {event.event_type.value} ({event.event_id})")

    def _persist_sqlite(self, event: AuditEvent):
        """Persist event to SQLite."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO audit_events (
                    event_id, timestamp, event_type, severity, exchange, symbol,
                    order_id, side, qty, price, fill_qty, fill_price, cumulative_qty,
                    position_qty, risk_reason, daily_loss_usd, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id,
                event.timestamp.isoformat(),
                event.event_type.value,
                event.severity.value,
                event.exchange,
                event.symbol,
                event.order_id,
                event.side,
                event.qty,
                event.price,
                event.fill_qty,
                event.fill_price,
                event.cumulative_qty,
                event.position_qty,
                event.risk_reason,
                event.daily_loss_usd,
                json.dumps(event.metadata) if event.metadata else None,
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to persist event to SQLite: {e}")

    def _persist_postgres(self, event: AuditEvent):
        """Persist event to PostgreSQL."""
        try:
            import psycopg2
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO audit_events (
                    event_id, timestamp, event_type, severity, exchange, symbol,
                    order_id, side, qty, price, fill_qty, fill_price, cumulative_qty,
                    position_qty, risk_reason, daily_loss_usd, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                event.event_id,
                event.timestamp,
                event.event_type.value,
                event.severity.value,
                event.exchange,
                event.symbol,
                event.order_id,
                event.side,
                event.qty,
                event.price,
                event.fill_qty,
                event.fill_price,
                event.cumulative_qty,
                event.position_qty,
                event.risk_reason,
                event.daily_loss_usd,
                event.metadata if event.metadata else None,
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to persist event to PostgreSQL: {e}")

    def get_events_by_type(self, event_type: EventType, limit: int = 1000) -> list[AuditEvent]:
        """Get recent events of a specific type."""
        matching = [e for e in self.events if e.event_type == event_type]
        return matching[-limit:]

    def get_events_by_date_range(
        self,
        start: datetime,
        end: datetime,
        limit: int = 10000
    ) -> list[AuditEvent]:
        """Get events within a date range."""
        matching = [e for e in self.events if start <= e.timestamp <= end]
        return matching[-limit:]

    def get_risk_events(self, limit: int = 1000) -> list[AuditEvent]:
        """Get all risk and warning events."""
        risk_types = {
            EventType.RISK_BLOCK,
            EventType.CIRCUIT_BREAKER_TRIGGERED,
            EventType.POSITION_LIMIT_BREACHED,
            EventType.DAILY_LOSS_LIMIT_BREACHED,
        }
        matching = [e for e in self.events if e.event_type in risk_types]
        return matching[-limit:]

    def get_filled_orders(self, symbol: str) -> list[AuditEvent]:
        """Get all fills for a symbol."""
        matching = [
            e for e in self.events
            if e.symbol == symbol and e.event_type in {EventType.PARTIAL_FILL, EventType.FULL_FILL}
        ]
        return matching

    def export_finra_trail(self, start: datetime, end: datetime, output_path: Optional[Path] = None) -> str:
        """
        Export audit trail in FINRA ATS format.
        
        FINRA requires:
        - All executed trades
        - Execution price and quantity
        - Execution time (to the second)
        - Side (buy/sell)
        - Account identifier
        
        Args:
            start: Start date for audit trail
            end: End date for audit trail
            output_path: Optional path to write CSV file
            
        Returns:
            CSV formatted audit trail
        """
        fills = self.get_events_by_date_range(start, end)
        fills = [e for e in fills if e.event_type in {EventType.PARTIAL_FILL, EventType.FULL_FILL}]
        
        # FINRA ATS Format (simplified)
        lines = [
            "Execution Time,Symbol,Quantity,Price,Side,Execution ID,Order ID"
        ]
        
        for fill in fills:
            line = (
                f"{fill.timestamp.isoformat()},"
                f"{fill.symbol},"
                f"{fill.fill_qty},"
                f"{fill.fill_price},"
                f"{fill.side},"
                f"{fill.event_id},"
                f"{fill.order_id}"
            )
            lines.append(line)
        
        csv_content = "\n".join(lines)
        
        if output_path:
            output_path.write_text(csv_content)
            logger.info(f"FINRA audit trail exported to {output_path}")
        
        return csv_content

    def get_summary(self) -> dict:
        """Get summary statistics of audit log."""
        return {
            "total_events": len(self.events),
            "event_type_counts": {
                et.value: len([e for e in self.events if e.event_type == et])
                for et in EventType
            },
            "severity_counts": {
                es.value: len([e for e in self.events if e.severity == es])
                for es in EventSeverity
            },
            "earliest": self.events[0].timestamp.isoformat() if self.events else None,
            "latest": self.events[-1].timestamp.isoformat() if self.events else None,
        }
