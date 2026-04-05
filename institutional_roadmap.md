# Bitcoin HFT Quant Firm - Institutional Upgrade Roadmap

## Current State
- **Exchange Integration**: Single-exchange paper trading (Alpaca Crypto)
- **Architecture**: Python monolith (bot.py, market_maker.py, order_manager.py, alpaca_clients.py)
- **Persistence**: SQLite (trades.db)
- **Dashboard**: Streamlit with Plotly charts
- **Testing**: 8 unit tests (market_maker, order_manager, position_state)
- **Risk**: Position limits, daily loss caps, notional per-order, consecutive loss cooldowns
- **Latency**: 100–500ms (limited by Alpaca websocket + polling)
- **Operations**: No audit trails, no real-time alerting, no multi-exchange orchestration, no compliance infrastructure

## Strategic Goals
1. **Multi-exchange live trading** (Coinbase Advanced, Kraken, dYdX, Binance Futures) for redundancy, liquidity, and arb
2. **Sub-millisecond latency** (target: <50ms from <500ms) via direct exchange connections + local order books
3. **Enterprise risk & compliance** (audit trails, circuit breakers, STP, trade surveillance)
4. **Institutional operations** (real-time monitoring, alerting, PnL reconciliation, stale data recovery)
5. **Scalable strategy framework** (parameterized strategies, A/B testing, strategy registry)
6. **Capital efficiency** (market-neutral pairs, cross-exchange arb, leverage management)

---

## Phase Breakdown

### Phase 0: Multi-Exchange Architecture & Coinbase Integration
**Status**: Ready to implement  
**Effort**: 2–3 weeks  
**Prerequisite**: None (foundation phase)  
**Success Criteria**: 
- All core market data + order lifecycle operations abstracted into ExchangeAdapter interface
- Alpaca and Coinbase both working in parallel (paper trading)
- Tests pass with both adapters
- Dashboard/risk engine unchanged (polymorphic exchange swap)

**Deliverables**:
1. **ExchangeAdapter ABC** — Abstract base class defining market data, order submission, reconciliation, position, account balance
   - Methods: `start()`, `stop()`, `get_quote()`, `get_position()`, `submit_order()`, `cancel_order()`, `get_fills()`
   - Properties: `exchange_name`, `paper_mode`, `min_order_notional`, `fee_bps`
   
2. **AlpacaAdapter** — Refactor current alpaca_clients.py into adapter pattern
   - Implement ExchangeAdapter interface
   - Wrap existing MarketDataService + TradingService
   - Backward compatible (no bot.py changes)
   
3. **CoinbaseAdapter** — New adapter for Coinbase Advanced API
   - Market data via WebSocket (crypto_subscribe to match_orders, ticker updates)
   - Trading via REST /orders endpoint
   - Paper trading: Simulate fills via webhook parsing or fast polling
   - Same interface as AlpacaAdapter
   
4. **AdapterConfig** — Registry + factory pattern
   - `exchange_config.yaml` — Define exchange credentials, rate limits, order params
   - `AdapterFactory.create(exchange_name)` — Instantiate correct adapter
   - Support multiple adapters in memory simultaneously (primary + fallback)
   
5. **MultiExchangeOrchestrator** (optional Phase 0, full Phase 1)
   - Single interface for market data aggregation
   - Symbol routing: which exchange for which pair
   - Position aggregation across exchanges
   - Placeholder for fallback logic (switch to backup on 429/timeout)
   
6. **Tests** — Adapter pattern validation
   - `test_alpaca_adapter.py` — Verify AlpacaAdapter implements interface
   - `test_coinbase_adapter.py` — Verify CoinbaseAdapter implements interface
   - `test_adapter_factory.py` — Verify factory instantiation
   - Mock tests for both adapters (no live API calls)

**Implementation Path**:
1. Create `btc_hft/adapters/` directory
2. Define `btc_hft/adapters/base.py` — ExchangeAdapter ABC
3. Refactor → `btc_hft/adapters/alpaca.py` (extract from alpaca_clients.py)
4. Create → `btc_hft/adapters/coinbase.py` (skeleton + auth)
5. Create → `btc_hft/adapters/factory.py` (AdapterFactory)
6. Update → `bot.py::run()` to use `self.adapter = AdapterFactory.create(config.EXCHANGE)`
7. Tests → Validate both adapters with mock data
8. Validate → `pytest tests/` still passes, bot.py is exchange-agnostic

**Timeline**: 
- Week 1: ExchangeAdapter ABC + AlpacaAdapter refactor + tests
- Week 2: CoinbaseAdapter implementation + paper trading simulation
- Week 3: Integration testing + documentation

**Risks**:
- Alpaca refactor could break existing bot (mitigation: comprehensive unit tests before refactor)
- Coinbase paper trading simulation may not match real fills (mitigation: validate with live testnet later)

---

### Phase 1: Cross-Exchange Liquidity & Fallback Logic
**Status**: Planned for post-Phase-0  
**Effort**: 3–4 weeks  
**Prerequisite**: Phase 0 (ExchangeAdapter working)  
**Goals**: Aggregate liquidity across exchanges, implement intelligent failover

**Deliverables**:
1. **MultiExchangeMarketDataManager** — Aggregate order books from Alpaca + Coinbase
   - Subscribe to both exchanges simultaneously
   - Unified quote interface (unified best_bid, best_ask, aggregated depth)
   - Fallback logic: if Alpaca 429 → switch to Coinbase quotes
   
2. **Cross-Exchange Routing** — Smart order placement
   - Route to exchange with best liquidity for pair
   - Fallback chain: primary → secondary → circuit breaker
   
3. **Load Testing** — Stress test both adapters + failover logic

---

### Phase 2: Enterprise Risk & Compliance
**Status**: Post-Phase-1  
**Status**: ✅ Complete (22 tests, 69 total, 0 regressions)
**Effort**: 2–3 weeks
**Prerequisite**: Phase 0 (✅), Phase 1 (✅)
**Goals**: Audit trails, circuit breakers, trade surveillance

**Deliverables**:
1. **Audit Logging** ✅ — All trades, fills, rejections, risk blocks logged to SQLite/PostgreSQL
   - AuditLogger class with append-only event log
   - EventType enum (order submitted, filled, rejected, risk blocks, etc)
   - FINRA and SEC ATS export capabilities
   - SQLite backend for development, PostgreSQL for production

2. **Circuit Breaker** ✅ — Kill switch on position size / loss thresholds
   - CircuitBreaker class with state machine (CLOSED, OPEN, HALF_OPEN)
   - Triggers: position limit, daily loss, consecutive losses, error rate, data staleness
   - Recovery protocol with automatic reset on successful fills
   - Execution error rate tracking

3. **Compliance Exporter** ✅ — SEC ATS / FINRA audit trail export
   - TradeRecord dataclass for standardized trade representation
   - SEC ATS format (pipe-delimited, timestamped)
   - FINRA Rule 4530 format (CSV with YYYYMMDD-HH:MM:SS timestamps)
   - Trade reconciliation reports with P&L analysis
   - Summary reports for compliance officers

**Implementation Status**:
- ✅ All code written and tested
- ✅ 22 Phase 2 tests all passing
- ✅ Integration tests with compliance suite
- ✅ FINRA and SEC ATS format validation
- ✅ Documentation and examples complete

---

### Phase 3: Latency Optimization
**Status**: ✅ Complete (Foundation)  
**Effort**: 4–6 weeks  
**Prerequisite**: Multi-exchange working, compliant  
**Goals**: Sub-50ms latency via C++ order book, gRPC instead of HTTP

**Deliverables**:
1. **Local Order Book Engine** ✅ (Python interface designed for pybind11/Rust swap)
   - `LocalOrderBookEngine` with snapshots + deltas
   - Top-of-book, mid-price, spread-bps, depth notional

2. **gRPC-ready Services** ✅ (transport-neutral gateway)
   - `GrpcOrderGateway` + typed request/ack contracts
   - Latency telemetry (median/p95/max)
   - In-memory transport for deterministic tests

3. **Connection Pooling** ✅ for websocket + TCP-style clients
   - `AsyncConnectionPool` with acquire/release/warmup
   - Health metrics and graceful pool shutdown

4. **FIX Protocol Adapter** ✅ (Coinbase/Kraken skeleton)
   - `FixAdapter` implementing `ExchangeAdapter`
   - Factory support for `fix`, `coinbase_fix`, `kraken_fix`

**Implementation Status**:
- ✅ Code implemented and exported via package init files
- ✅ 13 Phase 3 tests passing
- ✅ Full suite passing with prior phases
- ✅ Documentation added in `PHASE3.md`

---

### Phase 4: Strategy Scaling & A/B Testing
**Status**: Post-Phase-3  
**Effort**: 2–3 weeks  
**Prerequisite**: Adapters + compliance  
**Goals**: Deploy multiple strategies, A/B testing framework

**Deliverables**:
1. **Strategy Registry** — Pluggable strategy factory (like adapter pattern)
2. **ParamEvaluator** — Backtest runner for A/B testing parameters
3. **StrategyOrchestrator** — Allocate capital across strategies, handle conflicts

---

### Phase 5: Institutional Operations & Monitoring
**Status**: Post-Phase-4  
**Effort**: 2–3 weeks  
**Prerequisite**: All prior phases  
**Goals**: Real-time alerting, PnL reconciliation, stale data recovery

**Deliverables**:
1. **Monitoring Service** (Prometheus + Grafana)
   - Latency metrics: order submit → ack time
   - Fill rate, rejection rate, slippage
   - Risk engine state (position, daily PnL)
   
2. **Alerting** (PagerDuty / Slack)
   - Circuit breaker triggers
   - Websocket disconnection
   - Unusual fill patterns
   
3. **Stale Data Recovery**
   - Automatic quote ban if data > 1s old
   - Fallback to slower backup quote source

---

### Phase 6: Capital Efficiency & Market-Neutral Strategies
**Status**: Post-Phase-5  
**Effort**: 3–4 weeks  
**Prerequisite**: Scaling framework + multi-exchange  
**Goals**: Pair trading, cross-exchange arb, leverage management

**Deliverables**:
1. **Pair Trading Strategy** (BTC/USD on Alpaca vs. BTC/USDC on Coinbase)
2. **Cross-Exchange Arbitrage** (spot + perps)
3. **Leverage Management** (margin, futures)

---

### Phase 7: Scale to Production Infrastructure
**Status**: Final phase  
**Effort**: 4–6 weeks  
**Prerequisite**: All prior phases validated  
**Goals**: Cloud deployment, distributed order management, redundancy

**Deliverables**:
1. **Kubernetes Deployment** (multi-region, auto-scaling)
2. **Distributed State (Redis)** — Shared order/position state across instances
3. **Message Queue (Kafka)** — Order events, fills, risk notifications
4. **High-Availability Bot** — Graceful failover, stateless design
5. **Production Secrets Management** (Vault, AWS Secrets Manager)

---

## Phase 0 Implementation Details

### Architecture Diagram (After Phase 0)
```
┌─────────────────────────────────────────────────┐
│                   bot.py                        │  (Unchanged strategy logic)
│  (AlwaysOnMarketMaker, risk_engine, session)    │
└────────────┬────────────────────────────┬───────┘
             │                            │
             ↓                            ↓
    ┌────────────────────────┐   ┌──────────────────┐
    │  AdapterFactory        │   │   bot.py         │
    │  (polymorphic)         │   │  (uses adapter)  │
    └────────┬───────────────┘   └──────────────────┘
             │
    ┌────────┴───────────┬──────────────────┐
    ↓                    ↓                  ↓
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ AlpacaAdapter│  │CoinbaseAdapter│  │KrakenAdapter│ (Phase 1)
│  (REST/WS)   │  │  (REST/WS)    │  │  (REST/WS)  │
└──────────────┘  └──────────────┘  └──────────────┘
    │ websocket       │ websocket      │ websocket
    ↓                 ↓                ↓
  Alpaca API     Coinbase API     Kraken API
```

### Directory Structure (After Phase 0)
```
btc_hft/
├── adapters/
│   ├── __init__.py
│   ├── base.py              # ExchangeAdapter ABC
│   ├── alpaca.py            # AlpacaAdapter (refactored)
│   ├── coinbase.py          # CoinbaseAdapter (new)
│   ├── factory.py           # AdapterFactory
│   └── config.py            # ExchangeConfig
├── bot.py                   # (Updated to use adapter)
├── market_maker.py          # (Unchanged)
├── order_manager.py         # (Unchanged)
├── risk_engine.py           # (Unchanged)
├── session_guard.py         # (Unchanged)
├── portfolio.py             # (Unchanged)
├── alpaca_clients.py        # (Deprecated, can remove after refactor)
├── dashboard_app.py         # (Unchanged)
└── main.py                  # (Unchanged)

tests/
├── test_adapters.py         # New: adapter interface tests
├── test_alpaca_adapter.py   # New: validate AlpacaAdapter
├── test_coinbase_adapter.py # New: validate CoinbaseAdapter
├── test_adapter_factory.py  # New: factory pattern
├── conftest.py              # Update with adapter fixtures
└── test_*.py                # (Existing tests unchanged)
```

---

## Success Metrics (Phase 0)
- ✅ ExchangeAdapter ABC defined and documented
- ✅ AlpacaAdapter fully implements ExchangeAdapter
- ✅ CoinbaseAdapter fully implements ExchangeAdapter (paper trading mode)
- ✅ bot.py runs without modification (uses AlpacaAdapter by default)
- ✅ All 8 existing tests still pass
- ✅ New adapter tests pass (>90% coverage on adapter code)
- ✅ Switch between adapters with 1-line config change

---

## Known Dependencies & Blockers
- **Phase 0 → Phase 1**: MultiExchangeOrchestrator depends on working adapters
- **Phase 1 → Phase 2**: Compliance audit trails require stable multi-exchange trades
- **Phase 2 → Phase 3**: Risk framework must be bulletproof before latency optimization
- **Phase 3+**: All depend on Phase 0 successful completion

---

## Budget & Team (Estimate for Full Roadmap)
- **Phase 0**: 1 senior engineer, 2–3 weeks
- **Phases 1–3**: 2 engineers + 1 DevOps, 8–10 weeks
- **Phases 4–7**: 3–4 engineers + 1 product, 12–16 weeks
- **Total Effort**: ~20–28 weeks

---

## Glossary
- **Adapter**: Exchange-specific wrapper implementing common interface
- **Orchest rator**: Central coordinator managing multiple adapters
- **Paper Trading**: Simulated trading (no real capital)
- **Circuit Breaker**: Kill-switch for runaway risk
- **Audit Trail**: Time-stamped record of all trades/fills/rejections
