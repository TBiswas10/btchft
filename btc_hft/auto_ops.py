from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class AutoOpsDecision:
    should_stop: bool
    reason: str | None = None


class AutoOpsGuard:
    """Solo-operator auto-ops checks: stale feeds and abnormal slippage."""

    RECOVERABLE_STREAM_ISSUES = {"auto_stop_stale_feed", "auto_stop_stream_disconnected"}

    def __init__(self, stale_data_seconds: int, max_fill_slippage_usd: float) -> None:
        self.stale_data_seconds = max(1, stale_data_seconds)
        self.max_fill_slippage_usd = max(0.0, max_fill_slippage_usd)
        self.last_daily_report_day: str | None = None

    def check_health(self, data_age_seconds: float, latest_stream_health: dict | None = None) -> AutoOpsDecision:
        if latest_stream_health:
            healthy = bool(latest_stream_health.get("connected", True))
            if not healthy:
                return AutoOpsDecision(True, "auto_stop_stream_disconnected")

            # If explicitly marked as no real message yet, avoid stale-feed auto-stop during warmup.
            if "last_message_at" in latest_stream_health and latest_stream_health.get("last_message_at") is None:
                return AutoOpsDecision(False)

            stream_age = latest_stream_health.get("data_age_seconds")
            if isinstance(stream_age, (int, float)) and stream_age > self.stale_data_seconds:
                return AutoOpsDecision(True, "auto_stop_stale_feed")

        if data_age_seconds > self.stale_data_seconds:
            return AutoOpsDecision(True, "auto_stop_stale_feed")

        return AutoOpsDecision(False)

    def check_fill_slippage(self, slippage_usd: float) -> AutoOpsDecision:
        if slippage_usd > self.max_fill_slippage_usd:
            return AutoOpsDecision(True, "auto_stop_abnormal_slippage")
        return AutoOpsDecision(False)

    @classmethod
    def is_recoverable_stream_issue(cls, reason: str | None) -> bool:
        return reason in cls.RECOVERABLE_STREAM_ISSUES

    def should_emit_daily_report(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(timezone.utc)).date().isoformat()
        if self.last_daily_report_day == current:
            return False
        self.last_daily_report_day = current
        return True
