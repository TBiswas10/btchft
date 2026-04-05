from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

DB_DEFAULT = Path("runtime/trades.db")
REPORTS_DEFAULT = Path("runtime/reports")


@st.cache_data(ttl=10)
def load_fills(db_path: str) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()

    with sqlite3.connect(str(path)) as conn:
        query = """
        SELECT
            ts,
            symbol,
            side,
            qty,
            price,
            realized_pnl_usd,
            est_fee_usd,
            est_slippage_usd,
            funding_pnl_usd,
            order_id,
            client_order_id
        FROM fills
        ORDER BY id ASC
        """
        df = pd.read_sql_query(query, conn)

    if df.empty:
        return df

    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col in ["qty", "price", "realized_pnl_usd", "est_fee_usd", "est_slippage_usd", "funding_pnl_usd"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["net_pnl_usd"] = df["realized_pnl_usd"] - df["est_fee_usd"] - df["est_slippage_usd"] + df["funding_pnl_usd"]
    df["cum_net_pnl_usd"] = df["net_pnl_usd"].cumsum()
    return df


@st.cache_data(ttl=10)
def load_events(db_path: str) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()

    with sqlite3.connect(str(path)) as conn:
        query = """
        SELECT ts, event_type, payload_json
        FROM events
        ORDER BY id DESC
        LIMIT 500
        """
        df = pd.read_sql_query(query, conn)

    if df.empty:
        return df

    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


@st.cache_data(ttl=10)
def load_latest_report(reports_dir: str) -> dict:
    path = Path(reports_dir)
    if not path.exists():
        return {}

    candidates = sorted(path.glob("eod_report_*.json"))
    if not candidates:
        return {}

    return json.loads(candidates[-1].read_text(encoding="utf-8"))


@st.cache_data(ttl=10)
def load_report_history(reports_dir: str) -> pd.DataFrame:
    path = Path(reports_dir)
    if not path.exists():
        return pd.DataFrame()

    rows = []
    for report_path in sorted(path.glob("eod_report_*.json")):
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append(
            {
                "generated_at": payload.get("generated_at"),
                "sharpe": (payload.get("analytics") or {}).get("sharpe", 0.0),
                "win_rate": (payload.get("analytics") or {}).get("win_rate", 0.0),
            }
        )

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    frame["generated_at"] = pd.to_datetime(frame["generated_at"], utc=True, errors="coerce")
    frame = frame.sort_values("generated_at")
    return frame


def _get_latest_heartbeat(db_path: str) -> dict:
    import sqlite3
    import json

    path = Path(db_path)
    if not path.exists():
        return {}
    conn = sqlite3.connect(str(path))
    row = conn.execute(
        "SELECT payload_json FROM events WHERE event_type='heartbeat' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def render_overview(fills: pd.DataFrame, events: pd.DataFrame, report: dict) -> None:
    if fills.empty:
        st.info("No fills found yet. Start the bot and execute some trades.")
    else:
        total_trades = len(fills)
        wins = int((fills["realized_pnl_usd"] > 0).sum())
        win_rate = (wins / total_trades) * 100 if total_trades else 0.0
        realized = fills["realized_pnl_usd"].sum()
        fees = fills["est_fee_usd"].sum()
        slippage = fills["est_slippage_usd"].sum()
        funding = fills["funding_pnl_usd"].sum()
        net = fills["net_pnl_usd"].sum()

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Trades", f"{total_trades}")
        c2.metric("Win Rate", f"{win_rate:.2f}%")
        c3.metric("Realized PnL", f"${realized:,.2f}")
        c4.metric("Fees", f"${fees:,.2f}")
        c5.metric("Slippage", f"${slippage:,.2f}")
        c6.metric("Net PnL", f"${net:,.2f}")

        pnl_chart = px.line(fills, x="ts", y="cum_net_pnl_usd", title="Cumulative Net PnL", markers=True)
        pnl_chart.update_layout(margin=dict(l=10, r=10, t=40, b=10))
        hourly = fills.set_index("ts").resample("1H").size().reset_index(name="trade_count")
        trades_chart = px.bar(hourly, x="ts", y="trade_count", title="Trades per Hour")
        trades_chart.update_layout(margin=dict(l=10, r=10, t=40, b=10))
        side_chart = px.pie(fills, names="side", title="Trade Side Distribution")
        side_chart.update_layout(margin=dict(l=10, r=10, t=40, b=10))

        st.plotly_chart(pnl_chart, use_container_width=True)
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(trades_chart, use_container_width=True)
        with c2:
            st.plotly_chart(side_chart, use_container_width=True)

    st.subheader("Latest EOD Report")
    if report:
        st.json(report)
    else:
        st.caption("No EOD report found yet.")

    st.subheader("Recent Events")
    if events.empty:
        st.caption("No events available yet.")
    else:
        display = events.copy()
        display["ts"] = display["ts"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        st.dataframe(display, use_container_width=True, hide_index=True)


def render_intelligence(db_path: str, report_history: pd.DataFrame, report: dict) -> None:
    heartbeat = _get_latest_heartbeat(db_path)
    analytics = report.get("analytics", {}) if report else {}

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("OFI Score", f"{heartbeat.get('ofi_score', 0.0):+.3f}", heartbeat.get("ofi_strength", "unknown"))
    col2.metric("P(toxic)", f"{heartbeat.get('p_toxic', 0.0):.3f}", heartbeat.get("bayes_regime", "unknown"))
    queue_label = heartbeat.get("queue_position", "unknown")
    queue_latency = heartbeat.get("queue_latency_ms", 0.0)
    col3.metric("Queue", f"{queue_label} / {queue_latency:.0f}ms")
    liquidation = bool(heartbeat.get("liquidation_mode", False))
    if liquidation:
        col4.error("Liquidation mode active")
    else:
        col4.metric("Liquidation", "off")

    st.divider()
    st.subheader("Rolling Sharpe History")
    if report_history.empty:
        st.caption("No report history yet.")
    else:
        chart = px.line(report_history, x="generated_at", y="sharpe", title="Rolling Sharpe from EOD Reports")
        chart.update_layout(margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(chart, use_container_width=True)

    st.subheader("Latest Analytics Snapshot")
    st.json(analytics)


def render_calibration(report: dict) -> None:
    calibration = report.get("calibration", {}) if report else {}
    cols = st.columns(4)
    cols[0].metric("Gamma", f"{calibration.get('gamma', 0.0):.4f}")
    cols[1].metric("OFI Skew", f"{calibration.get('ofi_skew_bps', 0.0):.2f}")
    cols[2].metric("Min Edge", f"{calibration.get('edge_bps', 0.0):.1f} bps")
    cols[3].metric("Calibrations", f"{calibration.get('calibration_count', 0)}")

    st.subheader("Recent Calibration Steps")
    adjustments = calibration.get("adjustment_log", []) or []
    if not adjustments:
        st.caption("No calibration history yet.")
    else:
        frame = pd.DataFrame(adjustments[-10:])
        st.dataframe(frame, use_container_width=True, hide_index=True)


def render_regime(report: dict) -> None:
    analytics = report.get("analytics", {}) if report else {}
    regime_pnl = analytics.get("regime_pnl", {}) or {}
    queue_pnl = analytics.get("queue_pnl", {}) or {}

    regime_rows = []
    for regime, stats in regime_pnl.items():
        regime_rows.append(
            {
                "regime": regime,
                "total_usd": stats.get("total_usd", 0.0),
                "avg_usd": stats.get("avg_usd", 0.0),
                "win_rate": stats.get("win_rate", 0.0),
            }
        )

    queue_rows = []
    for queue_pos, stats in queue_pnl.items():
        queue_rows.append(
            {
                "queue_position": queue_pos,
                "avg_usd": stats.get("avg_usd", 0.0),
                "win_rate": stats.get("win_rate", 0.0),
            }
        )

    if regime_rows:
        regime_df = pd.DataFrame(regime_rows)
        regime_chart = px.bar(regime_df, x="regime", y="total_usd", title="PnL by Regime")
        regime_chart.update_layout(margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(regime_chart, use_container_width=True)
        st.dataframe(regime_df.sort_values("total_usd", ascending=False), use_container_width=True, hide_index=True)
    else:
        st.caption("No regime PnL yet.")

    if queue_rows:
        queue_df = pd.DataFrame(queue_rows)
        queue_chart = px.bar(queue_df, x="queue_position", y="avg_usd", title="Average PnL by Queue Position")
        queue_chart.update_layout(margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(queue_chart, use_container_width=True)
        st.dataframe(queue_df.sort_values("avg_usd", ascending=False), use_container_width=True, hide_index=True)
    else:
        st.caption("No queue position PnL yet.")


def main() -> None:
    st.set_page_config(page_title="BTC HFT Dashboard", layout="wide")
    st.title("BTC HFT Paper Dashboard")

    with st.sidebar:
        st.header("Data Source")
        db_path = st.text_input("SQLite DB Path", str(DB_DEFAULT))
        reports_dir = st.text_input("Reports Directory", str(REPORTS_DEFAULT))
        st.caption("Tip: Keep this app alongside the bot for local use, or point to mounted runtime paths in deployment.")

    fills = load_fills(db_path)
    events = load_events(db_path)
    report = load_latest_report(reports_dir)
    report_history = load_report_history(reports_dir)

    overview_tab, intelligence_tab, calibration_tab, regime_tab = st.tabs(["Overview", "Intelligence", "Calibration", "Regime"])
    with overview_tab:
        render_overview(fills, events, report)
    with intelligence_tab:
        render_intelligence(db_path, report_history, report)
    with calibration_tab:
        render_calibration(report)
    with regime_tab:
        render_regime(report)


if __name__ == "__main__":
    main()
