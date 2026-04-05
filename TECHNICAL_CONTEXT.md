# Bitcoin HFT Bot — Technical Context & Current State

**Last Updated:** April 4, 2026  
**Status:** Market maker implementation complete; websocket stable pending; ready for Phase 1 multi-exchange expansion

---

## 1. Current Architecture

### Strategy: AlwaysOnMarketMaker
- **Model**: Two-sided continuous quoting with inventory-based skewing
- **Behavior**: 
  - Buys at bid, sells at ask; quotes spread dynamically based on inventory ratio
  - If position is flat (0 BTC), bootstraps with initial buy before enabling sell quotes
  - If position grows (long), widens ask spread to encourage sells; tightens bid spread to discourage buys
  - If position shrinks (short/near-flat), widens bid spread to encourage buys; tightens ask spread to discourage sells
- **Risk Gates**: Position limits, daily loss caps, notional per-trade, consecutive loss cooldowns, session windows

### Core Files (btc_hft/)
| File | Purpose | Status |
|------|---------|--------|
| **bot.py** | Main event loop (quotes, fills, risk, session) | ✅ Stable; handles rejections gracefully |
| **market_maker.py** | Two-sided pricing logic with inventory skew | ✅ Stable; unit tested (4 tests) |
| **config.py** | Settings/validation from .env | ✅ Validation relaxed to 5.0 BTC max position |
| **order_manager.py** | Order lifecycle (submit, reconcile, cancel, replace) | ✅ Rejects logged instead of crashed |
| **alpaca_clients.py** | Alpaca API wrappers (quotes, orders, account) | ⚠️ Websocket returns HTTP 429 rate limits |
| **risk_engine.py** | Position/notional/daily loss validation | ✅ Stable |
| **session_guard.py** | Session window enforcement (UTC start/end) | ✅ Stable |
| **market_data_service.py** | Websocket data feed subscription | ⚠️ Stale on Alpaca 429 errors |

### Dashboard
| File | Purpose | Status |
|------|---------|--------|
| **dashboard_app.py** | Streamlit web app (metrics, charts, event stream) | ⚠️ Created; dependencies pending pip install |
| **.streamlit/config.toml** | Streamlit config (headless, XSRF, no telemetry) | ✅ Ready |

---

## 2. Risk Profile (Current .env)

```env
# Trade Sizing
ORDER_SIZE_BTC=0.5              # Base size per quote leg
MAX_POSITION_BTC=2.0            # Max long position
MAX_TRADE_NOTIONAL_USD=50000    # Dollar cap per order

# Risk Limits
MAX_DAILY_LOSS_USD=5000         # Stop-loss for entire session
MAX_CONSECUTIVE_LOSSES=8        # Consecutive losing trades before cooldown
COOLDOWN_SECONDS=3              # Wait time after consecutive losses

# Market Maker Params
market_maker_target_spread_bps=8              # Desired bid-ask spread (basis points)
market_maker_inventory_skew_bps=6             # How much to shift spread per 10% inventory deviation
market_maker_size_skew_factor=1.25            # Scale order size based on position
market_maker_reprice_bps=1.5                  # Min spread change to trigger reprice

# Exit Signals (Legacy, not used in MM mode)
TAKE_PROFIT_BPS=6
STOP_LOSS_BPS=6
MAX_HOLDING_SECONDS=10

# Session & Dashboard
SESSION_START_UTC=09:30
SESSION_END_UTC=16:00
DASHBOARD_INTERVAL_SECONDS=60   # Refresh interval (reduced from 2s to avoid noise)
```

**Rationale**: 0.5 BTC ≈ $20k at $40k/BTC; 2.0 BTC max = 4x leverage tolerance; 5000 USD daily loss = 2–3 bad trades before auto-stop.

---

## 3. Recent Code Changes

### Fix 1: Sell-Size Clamping (Latest)
**Problem**: Bot attempted to sell 0.65 BTC when only 0.49 BTC held → Alpaca rejected → infinite loop of rejections  
**Solution**: In `bot.py::_manage_quote_leg()`, clamp sell quantity to available inventory:
```python
if side == "sell":
    available_btc = max(self.state.position.qty_btc, 0.0)
    desired_qty = min(desired_qty, available_btc)
```
**Result**: Gracefully falls back to smaller orders instead of queueing rejections  
**Test Impact**: No regression (8/8 tests passing)

### Fix 2: Config Validation Relaxation
**Problem**: User wanted MAX_POSITION_BTC=2.0, but validation rejected anything > 1.0  
**Solution**: Relaxed hard limit in `config.py` from 1.0 → 5.0 BTC (still conservative for institutional context)  
**Result**: Config validation now passes for aggressive profile

### Earlier: Spot Inventory Bootstrap
**Problem**: Cannot short-sell BTC from flat position without inventory  
**Solution**: Added bootstrap logic: if position is 0, buy ORDER_SIZE_BTC before enabling sell quotes  
**Result**: Bot can now start quoting both sides after initial fill

### Earlier: Graceful Rejection Handling
**Problem**: `order_manager.py::submit()` raised exceptions on Alpaca 403/insufficient balance → crashed bot  
**Solution**: Changed to return `None` on exception; log rejection reason instead  
**Result**: Bot continues running despite rejected orders

---

## 4. Known Issues

### Issue 1: Websocket Rate Limiting (Alpaca HTTP 429)
**Symptom**: Bot starts successfully, but websocket connection returns `HTTP 429 Too Many Requests`  
**Root Cause**: Alpaca paper trading API enforces aggressive connection rate limits; may be shared infrastructure  
**Impact**: Market data feed goes stale; quotes become less responsive, but bot doesn't crash (handles gracefully)  
**Workaround**: 
- On startup, wait 30–60s before first quote
- Add exponential backoff to reconnection logic (not yet implemented)
- Check if another bot/process is connected to same account

**Next Step (Phase 1)**: Migrate to Coinbase Advanced API (higher rate limits, better liquidity)

### Issue 2: Streamlit Dependencies Not Installed
**Symptom**: `dashboard_app.py` imports `streamlit`, `plotly`, `pandas` but not in venv  
**Impact**: Cannot run `streamlit run dashboard_app.py` until installed  
**Solution**: Run `pip install -r requirements.txt` (Streamlit, Plotly, Pandas listed in requirements)

### Issue 3: Stale Market Data During 429 Errors
**Symptom**: During websocket downtime, last_quote_time is old; quotes may be based on stale prices  
**Impact**: Potential slippage if using cached quotes during data feed outage  
**Workaround**: `market_data_service.py` logs stale data; bot doesn't quote if quote age > 30s (safety check in place)

---

## 5. Test Suite Status

**Location**: `tests/`  
**Total**: 8 passing tests (no failures)

| Test File | Tests | Purpose |
|-----------|-------|---------|
| test_market_maker.py | 4 | Inventory skew logic, two-sided quoting |
| test_order_manager.py | 3 | Order submission, rejection handling |
| test_position_state.py | 1 | Position tracking |

**Last Run**: After sell-clamp fix (all passing, no regression)

**Command to Run Tests**:
```powershell
python -m pytest -v
```

---

## 6. Deployment Notes

### Local Development
```powershell
# Activate venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Run tests
python -m pytest -v

# Start bot
python main.py

# Start dashboard (separate terminal)
streamlit run dashboard_app.py
```

### Dashboard Deployment Options
1. **Streamlit Community Cloud** (recommended for MVP)
   - Push repo to GitHub
   - Connect repo to Streamlit Community Cloud
   - Auto-deploy on push
   - Free tier: 3 apps, 1GB storage

2. **Cloud VM** (AWS/GCP/Azure)
   - `streamlit run dashboard_app.py --server.port 8501`
   - Bind to `0.0.0.0:8501`
   - Add firewall rule for port 8501
   - Cost: $5–10/month for small t2.micro

3. **Docker** (if scalability needed)
   - Dockerfile provided in repo root
   - Run: `docker build -t btc-hft-dashboard . && docker run -p 8501:8501 btc-hft-dashboard`

---

## 7. Next Immediate Steps

**Priority 1 (This Week)**: Install Streamlit dependencies and test dashboard
```powershell
pip install -r requirements.txt
streamlit run dashboard_app.py
```

**Priority 2 (This Week)**: Debug websocket 429 errors
- Check if another process is connected to Alpaca account
- Add exponential backoff to websocket reconnection
- Consider reducing quote update frequency (currently ~100ms, could be 500ms)

**Priority 3 (Next Week)**: Phase 1 — Multi-Exchange Adapter
- Implement Coinbase Advanced API adapter (same OrderManager/MarketDataService interface)
- Test on Coinbase testnet
- Redirect live quotes to Coinbase instead of Alpaca (side-by-side during transition)

---

## 8. Code Quality & Conventions

### File Structure
```
btc_hft/
├── bot.py              # Main event loop
├── market_maker.py     # Strategy logic
├── config.py           # Settings
├── order_manager.py    # Order lifecycle
├── alpaca_clients.py   # Exchange API
├── risk_engine.py      # Risk validation
├── session_guard.py    # Session window
├── market_data_service.py  # Websocket
└── __init__.py

tests/
├── test_market_maker.py
├── test_order_manager.py
├── test_position_state.py
└── conftest.py

dashboard_app.py        # Streamlit UI
main.py                 # Entry point
.env                    # Runtime config
requirements.txt        # Python deps
```

### Type Hints
All functions have type hints. Key data classes:
- `PositionState` (qty_btc, entry_prices, cumulative_pnl)
- `ManagedOrder` (order_id, side, qty, price, status, fills)
- `QuotePlan` (bid_price, bid_qty, ask_price, ask_qty)
- `Settings` (frozen dataclass with validation)

### Logging
Uses `logging` module with persistent log file: `trading.log`
- `INFO`: Order submissions, fills, strategic decisions
- `WARNING`: Rejections, risk gate violations, stale data
- `ERROR`: Crashes, connection failures

---

## 9. Glossary

| Term | Definition |
|------|-----------|
| **Bot** | Main asyncio event loop (bot.py) coordinating quotes and fills |
| **Market Maker** | Strategy that quotes both bid and ask continuously |
| **Inventory Skew** | Adjusting bid/ask spread based on position size (long → widen ask, short → widen bid) |
| **Spot Bootstrap** | Buying initial BTC when position is flat before enabling sales |
| **Quote Leg** | One side of a two-sided quote (bid or ask); managed independently |
| **Alpaca** | Paper-trading broker API (current venue) |
| **Websocket** | Live market data feed subscription (quote ticks, fills) |
| **Risk Gate** | Validation check (position, notional, daily loss) that blocks orders |
| **Session Window** | Trading hours enforced by SESSION_START_UTC / SESSION_END_UTC |

---

## 10. Contact & Quick Reference

**Quick Commands**:
```powershell
# Activate environment
.\.venv\Scripts\Activate.ps1

# Run tests
python -m pytest -v

# Start bot
python main.py

# Start dashboard
streamlit run dashboard_app.py

# Check config
python -c "from btc_hft.config import settings; print(settings)"
```

**Key Config Parameters to Tune**:
- `ORDER_SIZE_BTC` — Trade size (currently 0.5 BTC)
- `market_maker_target_spread_bps` — Desired spread (currently 8 bps = 0.08%)
- `MAX_CONSECUTIVE_LOSSES` — Cooldown trigger (currently 8 losses)
- `COOLDOWN_SECONDS` — Pause duration (currently 3s)

**Monitoring**:
- Dashboard: `http://localhost:8501` (after `streamlit run`)
- Logs: `trading.log` (real-time activity)
- Database: `trades.db` (SQLite, queryable with DBeaver or CLI)

