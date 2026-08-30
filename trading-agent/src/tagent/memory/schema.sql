-- Trading agent persistent memory.
--
-- Design note: knowledge is split into three tiers that learn at DIFFERENT RATES,
-- because an agent placing ~10-40 trades a week will otherwise "learn" noise.
--
--   1. process rules  - deterministic mistakes (rule violations, cost errors).
--                       n=1 is enough. Promoted to hard constraints immediately.
--   2. setup stats    - claims about edge. Shrunk hard toward zero-edge until
--                       there are enough samples to justify moving position size.
--   3. calibration    - "when I say 70% confident, am I right 70% of the time?"
--                       The single most reliably learnable signal we have.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ---------------------------------------------------------------- decisions --
-- One row per proposal the agent made, whether or not it was executed.
-- Rejected proposals are as valuable as accepted ones: they are how we learn
-- which rules keep firing.
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY,
    ts              TEXT    NOT NULL,           -- ISO8601 UTC
    cycle_id        TEXT    NOT NULL,           -- groups decisions in one wake-up
    symbol          TEXT    NOT NULL,
    asset_class     TEXT    NOT NULL,           -- equity | option
    side            TEXT    NOT NULL,           -- buy | sell | buy_to_open | ...
    quantity        REAL    NOT NULL,
    order_type      TEXT    NOT NULL,
    limit_price     REAL,
    notional        REAL    NOT NULL,

    setup_tag       TEXT    NOT NULL,           -- the strategy family, e.g. "put_credit_spread"
    regime_tag      TEXT,                       -- coarse market state at decision time
    confidence      REAL    NOT NULL,           -- agent's stated P(win), 0..1
    thesis          TEXT    NOT NULL,           -- why, in the agent's own words
    features_json   TEXT    NOT NULL DEFAULT '{}',  -- structured inputs, for later retrieval

    gate_verdict    TEXT    NOT NULL,           -- allow | reject
    gate_reasons    TEXT    NOT NULL DEFAULT '[]',
    broker_order_id TEXT,
    status          TEXT    NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed','rejected','submitted','filled','cancelled','failed'))
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts     ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_decisions_setup  ON decisions(setup_tag, regime_tag);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_decisions_cycle  ON decisions(cycle_id);

-- ----------------------------------------------------------------- outcomes --
-- Written when a position opened by a decision is finally closed. This is the
-- ONLY source of truth for whether the agent is any good.
CREATE TABLE IF NOT EXISTS outcomes (
    decision_id     INTEGER PRIMARY KEY REFERENCES decisions(id) ON DELETE CASCADE,
    closed_ts       TEXT    NOT NULL,
    pnl             REAL    NOT NULL,           -- dollars, net of fees
    pnl_pct         REAL    NOT NULL,           -- return on capital at risk
    fees            REAL    NOT NULL DEFAULT 0,
    slippage        REAL    NOT NULL DEFAULT 0, -- fill vs. decision-time mid
    holding_days    REAL    NOT NULL,
    exit_reason     TEXT    NOT NULL,           -- target | stop | expiry | manual | risk_close
    was_win         INTEGER NOT NULL            -- 1/0, for calibration
);
CREATE INDEX IF NOT EXISTS idx_outcomes_closed ON outcomes(closed_ts);

-- ------------------------------------------------------------------ lessons --
-- The agent's journal. Written by the nightly review, read at every cycle.
--
-- scope='process' lessons are hard rules and may be promoted into the risk gate.
-- scope='edge' lessons are claims about profitability and are NOT allowed to
-- influence sizing until evidence_n clears the significance threshold.
CREATE TABLE IF NOT EXISTS lessons (
    id              INTEGER PRIMARY KEY,
    created_ts      TEXT    NOT NULL,
    updated_ts      TEXT    NOT NULL,
    scope           TEXT    NOT NULL CHECK (scope IN ('process','edge','regime')),
    setup_tag       TEXT,                       -- NULL = applies to everything
    regime_tag      TEXT,
    text            TEXT    NOT NULL,           -- imperative, specific, checkable
    evidence_ids    TEXT    NOT NULL DEFAULT '[]',  -- decision ids that justify it
    evidence_n      INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','retired','superseded')),
    retired_ts      TEXT,
    retired_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_lessons_active ON lessons(status, scope);

-- ------------------------------------------------------------- setup_stats --
-- Rolling realized expectancy per (setup, regime), recomputed by the review job.
-- posterior_* columns are shrunk toward zero edge; the agent and the gate read
-- the posterior, never the raw observed mean.
CREATE TABLE IF NOT EXISTS setup_stats (
    setup_tag         TEXT NOT NULL,
    regime_tag        TEXT NOT NULL DEFAULT '*',
    n                 INTEGER NOT NULL DEFAULT 0,
    wins              INTEGER NOT NULL DEFAULT 0,
    observed_mean_pct REAL    NOT NULL DEFAULT 0,
    observed_sd_pct   REAL    NOT NULL DEFAULT 0,
    posterior_mean_pct REAL   NOT NULL DEFAULT 0,
    posterior_se_pct  REAL    NOT NULL DEFAULT 0,
    total_pnl         REAL    NOT NULL DEFAULT 0,
    blocked           INTEGER NOT NULL DEFAULT 0,  -- gate refuses new entries
    updated_ts        TEXT    NOT NULL,
    PRIMARY KEY (setup_tag, regime_tag)
);

-- ------------------------------------------------------------- calibration --
-- Stated confidence vs. realized hit rate, bucketed. The agent sees this every
-- cycle so it can correct its own systematic over/under-confidence.
CREATE TABLE IF NOT EXISTS calibration (
    bucket        TEXT    PRIMARY KEY,     -- e.g. '0.6-0.7'
    n             INTEGER NOT NULL DEFAULT 0,
    wins          INTEGER NOT NULL DEFAULT 0,
    mean_stated   REAL    NOT NULL DEFAULT 0,
    realized_rate REAL    NOT NULL DEFAULT 0,
    updated_ts    TEXT    NOT NULL
);

-- ------------------------------------------------------------ equity_curve --
-- One row per cycle. Drives the drawdown kill switch, which must never depend
-- on the broker being reachable at the moment we need to stop.
CREATE TABLE IF NOT EXISTS equity_curve (
    ts           TEXT PRIMARY KEY,
    equity       REAL NOT NULL,
    cash         REAL NOT NULL,
    deployed_pct REAL NOT NULL DEFAULT 0,
    peak_equity  REAL NOT NULL,
    drawdown_pct REAL NOT NULL
);

-- ------------------------------------------------------------------ events --
-- Append-only operational log. Every gate rejection, auth failure, kill-switch
-- trip and review run lands here. This is what you read when something breaks.
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY,
    ts       TEXT NOT NULL,
    level    TEXT NOT NULL,          -- info | warn | error | critical
    kind     TEXT NOT NULL,
    message  TEXT NOT NULL,
    data     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

-- ------------------------------------------------------------------- state --
-- Small key/value store: kill switch, last review timestamp, auth status.
CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_ts TEXT NOT NULL
);
