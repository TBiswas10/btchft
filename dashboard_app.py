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

    df["net_pnl_usd"] = (
        df["realized_pnl_usd"]
        - df["est_fee_usd"]
        - df["est_slippage_usd"]
        + df["funding_pnl_usd"]
    )
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


def render_metrics(fills: pd.DataFrame) -> None:
    if fills.empty:
        st.info("No fills found yet. Start the bot and execute some trades.")
        return

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


def render_charts(fills: pd.DataFrame) -> None:
    if fills.empty:
        return

    pnl_chart = px.line(
        fills,
        x="ts",
        y="cum_net_pnl_usd",
        title="Cumulative Net PnL",
        markers=True,
    )
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


def render_latest_report(report: dict) -> None:
    st.subheader("Latest EOD Report")
    if not report:
        st.caption("No EOD report found yet.")
        return
    st.json(report)


def render_events(events: pd.DataFrame) -> None:
    st.subheader("Recent Events")
    if events.empty:
        st.caption("No events available yet.")
        return

    display = events.copy()
    display["ts"] = display["ts"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    st.dataframe(display, use_container_width=True, hide_index=True)


def render_ops_monitor(events: pd.DataFrame) -> None:
    st.subheader("Ops Monitor")
    if events.empty:
        st.caption("No monitoring events yet.")
        return

    recent = events.copy()
    recent = recent[recent["event_type"].isin(["auto_ops_stop", "risk_block", "stream_restart_requested", "alert_sent"])]

    if recent.empty:
        st.success("No recent critical ops alerts.")
        return

    latest = recent.iloc[0]
    st.error(f"Latest critical event: {latest['event_type']} @ {latest['ts']}")
    view = recent[["ts", "event_type", "payload_json"]].copy()
    view["ts"] = view["ts"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    st.dataframe(view, use_container_width=True, hide_index=True)


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

    render_metrics(fills)
    st.divider()
    render_charts(fills)
    st.divider()

    left, right = st.columns([1, 1])
    with left:
        render_latest_report(report)
    with right:
        render_events(events)

    st.divider()
    render_ops_monitor(events)


if __name__ == "__main__":
    main()
