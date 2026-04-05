# BTC Alpaca Paper Bot (MVP)

This project is a risk-first BTC paper-trading bot for Alpaca.
It now runs as an always-quoting market maker by default, with hard risk limits,
structured logging, SQLite persistence, and end-of-day report generation.

## What it does

- Streams BTC/USD quotes from Alpaca crypto websocket.
- Maintains simultaneous bid and ask quotes most of the time.
- Skews prices and sizes based on inventory so the bot leans back toward flat.
- Places limit orders through Alpaca paper trading API.
- Supports `DRY_RUN` mode (simulated fills, no API order submit).
- Tracks fills, realized PnL, estimated fees, and consecutive losses.
- Tracks estimated slippage and optional funding-style PnL.
- Applies hard risk gates (daily loss, stale data, position/notional limits).
- Enforces session windows, max trades per session, and daily resets.
- Stores events/fills in SQLite and emits JSON logs.
- Writes end-of-day JSON report at shutdown.
- Shows a simple live terminal dashboard for position and PnL.

## Quick start (from repository root)

1. Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Configure environment variables:

```powershell
Copy-Item .env.example .env
```

3. Fill in your Alpaca paper credentials in `.env`.

4. Run the bot from root:

```powershell
python main.py
```

## Modes

- Paper API mode: `DRY_RUN=false` (default), submits paper orders to Alpaca.
- Dry run mode: `DRY_RUN=true`, no order submits, orders are simulated and filled at limit price.
- Always-quoting mode: the bot keeps a bid and ask active and re-prices them as the market moves.

## Dashboard

- `DASHBOARD_ENABLED=true` to show a terminal panel.
- `DASHBOARD_INTERVAL_SECONDS=2` controls refresh cadence.

---

## Phase 2: Enterprise Risk & Compliance (New!)

The system now includes **production-grade compliance** with enterprise risk controls and audit logging.

### What's New

- **AuditLogger**: Immutable append-only event log (SQLite/PostgreSQL)
- **CircuitBreaker**: Automated kill-switch on position, loss, execution metrics
- **ComplianceExporter**: SEC ATS and FINRA audit trail generation
- **Risk Management**: Position limits, daily loss alerts, execution error tracking

### Learn More

See [PHASE2.md](PHASE2.md) for full architecture, APIs, and integration guide.

### Testing

```bash
pytest tests/test_phase2.py -v      # Phase 2 tests
pytest tests/ -q                     # All tests: 69/69 passing ✓
```

---


## Session Controls

- `SESSION_START_UTC` and `SESSION_END_UTC` define the allowed UTC trading window.
- `MAX_TRADES_PER_SESSION` stops the bot after too many trades in one session.
- Daily counters reset automatically when the UTC date changes.
 - **Phase 2** (✅ Complete): Enterprise risk & compliance framework
## Funding and Accounting

- `FUNDING_RATE_BPS_PER_HOUR=0` keeps funding disabled for spot-like behavior.
- If you later experiment with perps, this value lets the bot accrue funding PnL.

## Startup Check

- When `DRY_RUN=false`, the bot checks that the Alpaca paper account has enough buying power for the configured trade size.
- If the paper account is unfunded, it exits immediately with a clear message instead of crashing during order submission.

## Market Maker Tuning

- `MARKET_MAKER_TARGET_SPREAD_BPS` controls the total spread the bot tries to quote.
- `MARKET_MAKER_INVENTORY_SKEW_BPS` pushes quotes toward selling when long and buying when short.
- `MARKET_MAKER_SIZE_SKEW_FACTOR` changes how order size shifts with inventory.
- `MARKET_MAKER_REPRICE_BPS` controls how far the market can move before the bot replaces a quote.

## Spot Inventory Bootstrap

- On a spot paper account, the bot will first seed a small BTC inventory before it starts posting both bid and ask quotes.
- That avoids sell-order rejections when the account starts flat with zero BTC.

## Comparison Tool

Compare a backtest export against the paper-trading database:

```powershell
python compare_runs.py --expected path\to\backtest.csv --db runtime\trades.db
```

## Runtime artifacts

- Logs: `runtime/logs/bot.log`
- SQLite DB: `runtime/trades.db`
- Reports: `runtime/reports/eod_report_*.json`

## Streamlit Dashboard

Run locally:

```powershell
streamlit run dashboard_app.py
```

The dashboard reads:

- SQLite fills/events from `runtime/trades.db`
- Latest EOD report from `runtime/reports`

### Deploy and Access Anywhere

Option 1: Streamlit Community Cloud

1. Push this repository to GitHub.
2. In Streamlit Community Cloud, create a new app.
3. Set the main file path to `dashboard_app.py`.
4. Make sure deployment includes runtime data source (mounted volume, external DB, or synced files).

Option 2: Any cloud VM/container

```powershell
streamlit run dashboard_app.py --server.address 0.0.0.0 --server.port $env:PORT
```

Set `PORT` in your host environment (for example Railway/Render/Fly.io) and expose it publicly.

## Safety notes

- This is for paper trading only.
- Default config is conservative and long-only.
- Tune `MAX_DAILY_LOSS_USD` and `MAX_POSITION_BTC` before any live usage.

---

## Phase 1: Multi-Exchange Support (New!)

The bot now supports **multi-exchange functionality** with intelligent failover via Phase 1.

### What's New

- **MultiExchangeMarketDataManager**: Aggregate best bid/ask across multiple exchanges (e.g., Alpaca + Coinbase)
- **MultiExchangeOrderRouter**: Route orders with automatic failover if primary exchange fails
- **Health Monitoring**: Automatic detection and switching when exchanges degrade
- **Load Testing**: Built-in load tester for multi-exchange performance validation

### Quick Example

```python
from btc_hft.adapters import (
    AdapterFactory,
    MultiExchangeMarketDataManager,
    MultiExchangeOrderRouter,
)

# Create adapters
alpaca = AdapterFactory.create("alpaca", settings=config)
coinbase = AdapterFactory.create("coinbase")

# Create multi-exchange managers
market_mgr = MultiExchangeMarketDataManager(alpaca, [coinbase])
router = MultiExchangeOrderRouter(alpaca, [coinbase])

# Start and use
await market_mgr.start()
agg_quote = await market_mgr.get_aggregated_quote()
routed = await router.submit_order("BTCUSD", "buy", 0.1, 40000.0)
```

### Learn More

See [PHASE1.md](PHASE1.md) for:
- Full architecture and usage
- Load testing guide
- Performance characteristics
- Migration guide (Phase 0 → Phase 1)
- Comprehensive examples

### Testing

```bash
# Run Phase 1 tests
pytest tests/test_phase1.py -v

# Run all tests (Phase 0 + Phase 1)
pytest tests/ -q
# Expected: 47 tests passing
```

---

## Phase 2: Enterprise Risk & Compliance

Implemented in this repository:

- Audit logger with immutable event records and SQLite/PostgreSQL backends
- Circuit breaker with OPEN/HALF_OPEN/CLOSED states
- FINRA/SEC ATS export-ready compliance reporting

Learn more: [PHASE2.md](PHASE2.md)

```bash
# Run Phase 2 tests
pytest tests/test_phase2.py -v
```

---

## Phase 3: Latency Optimization Foundations

Implemented in this repository:

- Local in-memory order book engine with top-of-book and depth calculations
- Async connection pool for low-overhead connection reuse
- gRPC-ready order gateway interface with latency telemetry
- FIX adapter skeleton integrated into AdapterFactory

Learn more: [PHASE3.md](PHASE3.md)

```bash
# Run Phase 3 tests
pytest tests/test_phase3.py -v

# Run all tests (Phase 0 + 1 + 2 + 3)
pytest tests/ -q
# Expected: 82 tests passing
```

---

## Roadmap

This project follows a structured 7-phase institutional upgrade path:

- **Phase 0** (✅ Complete): Multi-exchange adapter pattern & Alpaca refactor
- **Phase 1** (✅ Complete): Cross-exchange liquidity & failover logic
- **Phase 2** (✅ Complete): Enterprise risk & compliance framework
- **Phase 3** (✅ Complete - Foundation): Latency optimization primitives & FIX/gRPC-ready interfaces
- **Phase 4** (Planned): Strategy scaling & A/B testing
- **Phase 5** (Planned): Institutional operations & monitoring
- **Phase 6** (Planned): Capital efficiency & market-neutral strategies
- **Phase 7** (Planned): Production infrastructure (K8s, distributed state)

See [institutional_roadmap.md](institutional_roadmap.md) for full details.

---

## Solo Operator Mode (Implemented)

This repo now includes a solo-friendly operational bundle with one market-neutral strategy path, auto-operations, lightweight alerting, and weekly champion/challenger experiments.

### 1) One Market-Neutral Strategy

Implemented module:
- [btc_hft/market_neutral.py](btc_hft/market_neutral.py)

Strategy:
- `SimpleHedgeArbitrage` evaluates one hedge/arbitrage path between two venues
- Enforces strict position cap and max leg size
- Requires minimum edge threshold before trading

### 2) Auto-Operations

Implemented modules:
- [btc_hft/auto_ops.py](btc_hft/auto_ops.py)
- Integrated in runtime loop: [btc_hft/bot.py](btc_hft/bot.py)

Behavior:
- Daily auto-report generation (`daily_auto_report` events)
- Auto-stop on stale feeds (`auto_ops_stop`)
- Auto-stop on abnormal fill slippage

Environment flags (see [.env.example](.env.example)):
- `AUTO_OPS_ENABLED=true`
- `DAILY_AUTO_REPORT_ENABLED=true`
- `MAX_FILL_SLIPPAGE_USD=2.5`

### 3) Lightweight Monitoring + Alert Channel

Implemented modules:
- [btc_hft/alerts.py](btc_hft/alerts.py)
- Dashboard monitor panel: [dashboard_app.py](dashboard_app.py)

Supported channels:
- `disabled`
- `discord` via webhook
- `telegram` via bot token + chat id
- `email` via SMTP

Key env settings:
- `ALERT_CHANNEL`
- `ALERT_WEBHOOK_URL`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `ALERT_SMTP_*`, `ALERT_EMAIL_TO`

### 4) Deployment Hardening

Implemented deploy target:
- [Dockerfile](Dockerfile)
- [docker-compose.yml](docker-compose.yml)

Features:
- Reproducible containerized runtime
- `restart: unless-stopped`
- Mounted `runtime/` volume for persistent DB/reports
- Container healthcheck

DB backup/restore tools:
- Backup: [scripts/backup_runtime_db.py](scripts/backup_runtime_db.py)
- Restore: [scripts/restore_runtime_db.py](scripts/restore_runtime_db.py)

Examples:

```powershell
# Build + run
docker compose up -d --build

# Backup DB
python scripts/backup_runtime_db.py --db runtime/trades.db --out-dir runtime/backups

# Restore DB
python scripts/restore_runtime_db.py --backup runtime/backups/trades_YYYYMMDD_HHMMSS.db --db runtime/trades.db
```

### 5) Weekly Champion/Challenger Cadence

Implemented modules:
- [btc_hft/experiments.py](btc_hft/experiments.py)
- Runner: [run_weekly_sweep.py](run_weekly_sweep.py)

This is intentionally a single pair (`champion` vs `challenger`) and writes a weekly winner report.

```powershell
python run_weekly_sweep.py --db runtime/trades.db --out runtime/reports/weekly_sweep.json
```

### Validation

```powershell
python -m pytest tests/test_solo_ops.py -q
python -m pytest tests/ -q
```
