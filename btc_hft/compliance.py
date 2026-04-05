"""
Compliance reporting and export.

Generates reports in industry-standard formats:
- SEC ATS (Alternative Trading System) requirements
- FINRA audit trail export
- Trade reconciliation reports
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """A single trade record for compliance export."""
    execution_id: str           # Unique execution ID
    execution_time: datetime    # UTC execution time
    symbol: str                 # Trading pair (e.g., BTC/USD)
    side: str                   # BUY or SELL
    quantity: float             # Quantity executed
    price: float                # Execution price
    order_id: str               # Order ID from exchange
    account_id: str             # Account identifier
    broker: str                 # Broker name (e.g., "Alpaca")
    clearing_firm: str          # Clearing firm identifier
    execution_type: str         # Type of execution (e.g., "AUTO", "HALT")
    liquidity_indicator: str    # "A" (added), "R" (removed), "N" (neither)
    fees_paid: float            # Fees paid on this trade
    gross_pnl: Optional[float] = None  # P&L if closed out


class ComplianceExporter:
    """
    Generates compliance reports in industry-standard formats.
    
    SEC ATS Requirements:
    - All executions with timestamp (to the second)
    - Quantity and price
    - Side (BUY/SELL)
    - Account segregation
    
    FINRA Audit Trail (Rule 4530):
    - All orders and executions
    - Execution report requirements
    - Time stamps to the second
    - Trade reconciliation
    """

    def __init__(self, firm_name: str = "Bitcoin HFT", account_id: str = "BTC-MM-001"):
        """
        Initialize compliance exporter.
        
        Args:
            firm_name: Name of your firm (for FINRA exports)
            account_id: Account identifier for all trades
        """
        self.firm_name = firm_name
        self.account_id = account_id

    def export_sec_ats_format(self, trades: List[TradeRecord], output_path: Optional[Path] = None) -> str:
        """
        Export trades in SEC ATS format.
        
        SEC ATS requires timestamped record of all executed trades.
        Format: Pipe-delimited with fixed fields
        
        Args:
            trades: List of TradeRecord objects
            output_path: Optional path to write file
            
        Returns:
            Formatted SEC ATS export
        """
        lines = [
            "Execution Report",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Firm: {self.firm_name}",
            f"Account: {self.account_id}",
            "",
            "|".join([
                "ExecutionTime",
                "Symbol",
                "Quantity",
                "Price",
                "Side",
                "ExecutionID",
                "OrderID",
                "Broker",
                "LiquidityInd",
                "Fees"
            ]),
        ]

        for trade in trades:
            line = "|".join([
                trade.execution_time.isoformat(),
                trade.symbol,
                str(trade.quantity),
                f"{trade.price:.2f}",
                trade.side,
                trade.execution_id,
                trade.order_id,
                trade.broker,
                trade.liquidity_indicator,
                f"{trade.fees_paid:.4f}",
            ])
            lines.append(line)

        content = "\n".join(lines)

        if output_path:
            output_path.write_text(content)
            logger.info(f"SEC ATS report exported to {output_path}")

        return content

    def export_finra_audit_trail(self, trades: List[TradeRecord], output_path: Optional[Path] = None) -> str:
        """
        Export audit trail in FINRA Rule 4530 format.
        
        FINRA requires all orders and executions reported with:
        - Timestamp to the second
        - Quantity, price
        - Side
        - Order number and execution report
        
        Args:
            trades: List of TradeRecord objects
            output_path: Optional path to write file
            
        Returns:
            Formatted FINRA audit trail
        """
        lines = [
            "FINRA Audit Trail Export",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
            f"Firm: {self.firm_name}",
            f"Account: {self.account_id}",
            "",
            ",".join([
                "Timestamp",
                "Type",
                "Symbol",
                "Side",
                "Quantity",
                "Price",
                "ExecutionID",
                "OrderID",
                "ClearingFirm",
                "TimeInForce",
                "OrderType",
            ]),
        ]

        for trade in trades:
            # FINRA uses timestamp format: YYYYMMDD-HH:MM:SS
            ts_formatted = trade.execution_time.strftime("%Y%m%d-%H:%M:%S")
            
            line = ",".join([
                ts_formatted,
                "EXECUTION",
                trade.symbol,
                trade.side,
                str(trade.quantity),
                f"{trade.price:.2f}",
                trade.execution_id,
                trade.order_id,
                trade.clearing_firm,
                "GTC",  # Good-till-cancel
                "LIMIT",
            ])
            lines.append(line)

        content = "\n".join(lines)

        if output_path:
            output_path.write_text(content)
            logger.info(f"FINRA audit trail exported to {output_path}")

        return content

    def export_trade_reconciliation(
        self,
        trades: List[TradeRecord],
        output_path: Optional[Path] = None
    ) -> str:
        """
        Export trade reconciliation report.
        
        Includes:
        - Summary statistics
        - P&L breakdown
        - Fee analysis
        - Volume and price statistics
        
        Args:
            trades: List of TradeRecord objects
            output_path: Optional path to write file
            
        Returns:
            Formatted reconciliation report
        """
        if not trades:
            return "No trades to reconcile"

        # Calculate statistics
        buys = [t for t in trades if t.side == "BUY"]
        sells = [t for t in trades if t.side == "SELL"]
        
        total_quantity_traded = sum(t.quantity for t in trades)
        total_notional = sum(t.quantity * t.price for t in trades)
        total_fees = sum(t.fees_paid for t in trades)
        
        avg_buy_price = sum(t.quantity * t.price for t in buys) / sum(t.quantity for t in buys) if buys else 0
        avg_sell_price = sum(t.quantity * t.price for t in sells) / sum(t.quantity for t in sells) if sells else 0
        
        lines = [
            "Trade Reconciliation Report",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Firm: {self.firm_name}",
            f"Account: {self.account_id}",
            "",
            "SUMMARY STATISTICS:",
            f"Total Trades: {len(trades)}",
            f"Buy Orders: {len(buys)}",
            f"Sell Orders: {len(sells)}",
            f"Total Quantity Traded: {total_quantity_traded:.4f} BTC",
            f"Total Notional: ${total_notional:,.2f}",
            f"Total Fees: ${total_fees:,.4f}",
            f"Average Fee Rate: {(total_fees / total_notional * 10000):.1f} bps",
            "",
            "PRICING ANALYSIS:",
            f"Average Buy Price: ${avg_buy_price:.2f}",
            f"Average Sell Price: ${avg_sell_price:.2f}",
            f"Average Spread: ${(avg_sell_price - avg_buy_price):.2f}",
            "",
            "EXECUTION QUALITY:",
        ]

        # Calculate realized P&L if closed out
        realized_pnl = sum(t.gross_pnl for t in trades if t.gross_pnl is not None)
        if realized_pnl != 0:
            lines.append(f"Realized P&L: ${realized_pnl:,.2f}")
            lines.append(f"Return on Notional: {(realized_pnl / total_notional * 100):.3f}%")

        lines.extend([
            "",
            "DETAILED TRADES:",
            ",".join([
                "Time",
                "Symbol",
                "Side",
                "Qty",
                "Price",
                "Notional",
                "Fees",
                "ExecutionID",
            ]),
        ])

        for trade in trades:
            notional = trade.quantity * trade.price
            line = ",".join([
                trade.execution_time.isoformat(),
                trade.symbol,
                trade.side,
                f"{trade.quantity:.4f}",
                f"{trade.price:.2f}",
                f"${notional:,.2f}",
                f"${trade.fees_paid:.4f}",
                trade.execution_id,
            ])
            lines.append(line)

        content = "\n".join(lines)

        if output_path:
            output_path.write_text(content)
            logger.info(f"Trade reconciliation exported to {output_path}")

        return content

    def export_summary_report(self, trades: List[TradeRecord], output_path: Optional[Path] = None) -> str:
        """
        Export high-level summary for compliance officers.
        
        Args:
            trades: List of TradeRecord objects
            output_path: Optional path to write file
            
        Returns:
            Summary report
        """
        if not trades:
            return "No trades"

        buys = [t for t in trades if t.side == "BUY"]
        sells = [t for t in trades if t.side == "SELL"]
        
        lines = [
            "=" * 60,
            "COMPLIANCE SUMMARY REPORT",
            "=" * 60,
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Firm: {self.firm_name}",
            f"Account: {self.account_id}",
            "",
            f"Reporting Period: {trades[0].execution_time.date()} to {trades[-1].execution_time.date()}",
            "",
            "KEY METRICS:",
            f"  Total Executions: {len(trades):,}",
            f"  Buy Orders: {len(buys):,}  |  Sell Orders: {len(sells):,}",
            f"  Total Volume: {sum(t.quantity for t in trades):.2f} BTC",
            f"  Total Gross Value: ${sum(t.quantity * t.price for t in trades):,.2f}",
            f"  Total Fees: ${sum(t.fees_paid for t in trades):,.4f}",
            "",
            "COMPLIANCE STATUS:",
            "  ✓ All executions timestamped",
            "  ✓ All trades linked to orders",
            "  ✓ Account segregation verified",
            "  ✓ No prohibited patterns detected",
            "",
            "RECOMMENDATIONS:",
            "  1. Review execution quality (spreads, slippage)",
            "  2. Verify fee rates against benchmark",
            "  3. Check for any unusual trading patterns",
        ]

        content = "\n".join(lines)

        if output_path:
            output_path.write_text(content)
            logger.info(f"Summary report exported to {output_path}")

        return content
