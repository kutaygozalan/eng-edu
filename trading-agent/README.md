# tagent — a scheduled trading agent with durable memory

A trading agent that wakes on a schedule during market hours, proposes trades,
passes them through a deterministic risk gate, and keeps a journal it actually
learns from.

**Status: complete loop built and tested (148 tests passing) — proposal,
risk gate, execution, outcome reconciliation, and nightly learning. Wired to
Robinhood's agentic MCP server. `dry_run` defaults to true.**

---

## The two design decisions that matter

### 1. The risk gate is code, and there is no override path

Everything above `risk/gate.py` is a proposal engine. The gate is deterministic
Python — no LLM call, no prompt, no way for a persuasive thesis to talk its way
past a position limit. `GateResult` is frozen, so a caller cannot flip a
rejection into an approval.

It also collects *every* reason a proposal fails rather than short-circuiting on
the first, because the rejection log is a primary input to the nightly review: a
rule that keeps firing tells you something about the agent, not the rule.

Two deliberate asymmetries:

- **Sizing is measured on max loss, not notional.** A defined-risk spread has
  large notional and small max loss. Sizing on notional would block every spread
  and wildly undersize equity.
- **Exits are held to a much looser standard than entries.** Refusing to let the
  agent reduce risk is strictly worse than letting it. The kill switch blocks new
  entries and permits closes.

### 2. Knowledge is split into three tiers that learn at different rates

This is the part that makes "gets better every day" real rather than a slogan.
An agent placing 10–40 trades a week will otherwise learn noise — and an agent
that reads its own noise as signal will lever into it.

| Tier | Example | Evidence needed | Why |
|---|---|---|---|
| **Process** | "Never submit inside the closing blackout" | **n = 1** | A rule violation is deterministic. One occurrence is proof. |
| **Edge** | "Put credit spreads work in calm regimes" | **n ≥ 30**, and shrunk | A claim about profitability. Four lucky trades are not evidence. |
| **Calibration** | "When I say 80%, I win 55%" | continuous | The most reliably learnable signal available. |

Edge claims run through Bayesian shrinkage toward a **zero-edge prior** with 20
pseudo-observations. Five trades at +20% produce a posterior under +4%. The agent
reads the posterior; it never sees the raw mean in a form it can size on.

A setup is blocked outright only when the posterior is negative by more than two
standard errors with n ≥ 15 — so a genuine loser gets shut off, but three bad
trades or a noisy sample does not cause thrashing.

The nightly review may also **retire** lessons whose evidence has decayed. A
journal that only grows becomes a list of superstitions.

---

## Architecture

```
cron (every 20 min, 09:45–15:25 ET)
   │
   ├─► cycle: RECONCILE fills → load memory → bounded context → LLM proposes
   │            → RISK GATE (deterministic) → broker → ledger
   │
   └─► review (17:30 ET): recompute expectancy, update calibration,
                          write/retire lessons, block failing setups
```

### How a trade becomes a lesson

```
decision ──fill──► lot ──close──► outcome ──nightly──► expectancy + calibration
   │                │                                        │
   │                └─ carries the OPENING decision_id        └─► journal
   └─ rejected proposals are recorded too, and reviewed
```

The lot is what makes attribution work. An exit is not its own trade — it is the
resolution of an earlier one — so P&L attributes back to the decision that
*opened* the position. Without that, every exit looks like a fresh trade and
per-setup expectancy is meaningless.

`reconcile.py` survives the four things that actually happen in real accounts:
partial fills, partial closes days apart, positions closed outside the agent
(sold in the app, assigned), and being run every 20 minutes without ever
double-booking a fill. It also holds a 5-minute grace period before judging a
position "externally closed", because fills and positions come from different
broker endpoints and do not update atomically — without it, a fresh fill gets
closed as external microseconds later and fabricates an outcome.

**One-shot invocations, not a long-running process.** The container starts, runs
one cycle, writes to SQLite, and exits. Nothing survives in RAM — which is
exactly what forces every piece of accumulated knowledge onto disk. Tomorrow's
agent starts from the same journal today's agent wrote.

```
src/tagent/
  clock.py              market hours, holidays, half-days, next-run
  config.py             typed config; unknown keys are errors, not shrugs
  risk/gate.py          the gate — deterministic, no override path
  memory/
    schema.sql          decisions, outcomes, lessons, setup_stats, calibration
    store.py            SQLite persistence
    review.py           shrinkage, calibration, lesson gating and retirement
  brokers/base.py       narrow broker interface (paper | alpaca | robinhood_mcp)
```

---

## Broker paths

The broker interface is deliberately narrow because this layer is the least
stable thing in the system: Robinhood's agentic MCP is three months old, options
support is still rolling out, and there is a
[known OAuth bug](https://github.com/anthropics/claude-code/issues/65895) where
Claude Code persists an empty access token against Robinhood MCP servers.

| Path | Use for |
|---|---|
| `paper` | Development. No network, deterministic fills. |
| `alpaca` | **Validation.** Free paper accounts get Level-3 options automatically. This is where the track record gets built. |
| `robinhood_mcp` | Live, once there is a track record worth risking. |

**Headless OAuth works**: on a server you print the authorize URL, approve it in
your own browser, and the token lands on the box. Two gotchas the deployment
handles: the token file must stay **read-write** (refresh re-encrypts in place),
and re-auth is periodic and manual by design — so `tagent health` runs at 08:00
and alerts, rather than letting the agent discover it at 09:45.

---

## Running it

```bash
pip install -e ".[dev]"
pytest -q                                    # 90 tests

cp config/config.example.yaml config.yaml    # edit limits + universe
tagent cycle  --config config.yaml           # one cycle (dry_run)
tagent review --config config.yaml           # nightly journal
tagent health --config config.yaml           # broker/auth check
```

Deployment lives in `deploy/`: a one-shot Dockerfile, a verified cron schedule,
and a wrapper that alerts on failure — because an agent that dies quietly is
worse than one that never ran.

---

## Safety posture

- `dry_run: true` by default. The agent reasons, proposes and records
  everything, but places no orders.
- Risk limits live in version-controlled YAML. Changing one is a reviewable diff.
- `release_kill_switch()` is operator-only. Nothing in the agent calls it.
- Secrets come from the environment. Never from config, never from the repo.
- Treat all fetched news as hostile input — the gate sits between the reasoning
  layer and the broker precisely so a poisoned headline cannot become an order.

## Not financial advice

See `../research/robinhood-trading-bot/` for the evidence this design is built
on, including why the return target that motivated it is not achievable and what
is.
