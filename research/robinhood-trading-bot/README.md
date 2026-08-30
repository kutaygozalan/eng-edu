# Automated Robinhood Trading with an LLM Agent Layer — Research Report

**Prepared:** August 30, 2026
**Question asked:** Can I build a bot that trades my Robinhood account continuously, trade options
through it, add a second agent that reads news/politics/government activity to inform it, and 3–4x
my capital in 12 months?

**Short answer:** The first three are now genuinely buildable, and the path is much better than it
was a year ago. The fourth — 3–4x in 12 months — is the part that does not hold up. Sections 6 and 7
work through why, with numbers rather than opinion, and Section 8 proposes what to build instead.

---

## 1. Bottom line up front

| Your goal | Verdict | Detail |
|---|---|---|
| Bot trades Robinhood directly | **Yes — officially supported now** | Robinhood shipped *Agentic Trading* (MCP server) in May 2026. §2 |
| Trade options | **Yes** | Robinhood lists options as supported; Alpaca offers full Level‑3 multi-leg via API. §4 |
| Secondary news/politics agent | **Yes, and the pattern is well-trodden** | §5. But measured alpha is small and decays fast. |
| "Trades constantly" | **Possible, but a liability, not a feature** | §7. Trade frequency is negatively correlated with retail returns in every study. |
| **3–4x in 12 months** | **Not realistically achievable without a high probability of large loss** | §6. |

The single most useful number in this report: **the best published LLM trading agents of 2026,
measured in live paper trading, returned 8–16% annualized.** Not 300%. Those same systems showed
20% backtested returns before live deployment. The gap is the whole story.

---

## 2. How to connect to Robinhood (three paths, ranked)

### Path A — Robinhood Agentic Trading (official, recommended)

Robinhood announced [Agentic Trading](https://robinhood.com/us/en/newsroom/robinhood-is-now-open-to-agents/)
on **May 27, 2026**. This is a first-party MCP server, purpose-built for exactly what you're asking for.

- **Endpoint:** `https://agent.robinhood.com/mcp/trading`
- **Setup:** `claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`,
  then an OAuth flow. Desktop only — you cannot authenticate an agentic account from mobile.
- **Clients supported:** Claude Desktop, Claude Code, ChatGPT, Codex / Codex CLI, Cursor, Grok, and any
  MCP-compatible client.
- **Account model:** trades happen in a **dedicated Agentic account**, separate from your main portfolio.
  You fund it deliberately. The agent gets read access to balances, positions, order history and
  watchlists across accounts, but **can only place orders in the agentic account**. You may hold up to
  10 self-directed accounts total, and you need an existing individual account in good standing first.
- **Asset classes:** launched equities-only in beta; Robinhood's product page now states
  *"available for equities, options, and crypto through Robinhood's MCP server."* Crypto was added
  around July 20, 2026. Options support has been rolling out gradually — **verify what your specific
  account can actually do before designing around it.** Event contracts and futures are on the roadmap.
- **Cost:** free for eligible US customers.

**The constraint that matters most for your goal:** *margin borrowing is not enabled for Agentic
accounts.* You can open one as a **limited margin** account, which lets you trade unsettled proceeds
without waiting for settlement — but it explicitly provides **no borrowing power and no leverage**,
creates no margin loan, and carries no margin-call risk. On a cash agentic account you wait one
business day for stock and option proceeds to settle.

This is a well-designed guardrail, and it is also a hard ceiling on the return profile. See §6.

**Guardrails Robinhood provides:** per-trade push notifications with live P&L, configurable capital
allocation and alert thresholds, disconnect-anytime from the app, and an optional
review-before-execute mode. That last one is important and under-documented: *if you tell the agent to
act without asking, it will place trades without confirming.* Robinhood is explicit that it
"does not control, supervise, monitor, recommend, or audit these AI agents."

**The practical gotcha for "constantly":** in a Claude Code setup, the client has to be running for the
agent to act. A laptop that sleeps is a bot that stops. Genuine 24/5 operation means hosting the agent
loop on a server you control, not on your desktop.

### Path B — `robin_stocks` and other unofficial libraries (not recommended in 2026)

[`jmfernandes/robin_stocks`](https://github.com/jmfernandes/robin_stocks) is the long-standing Python
wrapper around Robinhood's private mobile API. It supports stocks, options and crypto, and for years it
was the only game in town.

It is now the wrong choice, for three converging reasons:

1. **It keeps breaking.** Robinhood has repeatedly tightened authentication — device verification and
   changed MFA flows — and the repo's issue tracker reflects a recurring cycle of login breakage
   (issues [#521](https://github.com/jmfernandes/robin_stocks/issues/521),
   [#530](https://github.com/jmfernandes/robin_stocks/issues/530),
   [#1621](https://github.com/jmfernandes/robin_stocks/issues/1621)).
   Reverse-engineered auth against a hostile-to-automation endpoint is a permanent maintenance tax.
2. **It's against the terms.** Robinhood's ToS prohibit unauthorized automated access. The realistic
   downside isn't a lawsuit — it's account restriction at the worst possible moment, with positions open.
3. **There is now a sanctioned alternative that does the same job.** Path A exists specifically to
   remove the reason anyone used Path B. Robinhood's own framing: agents get "direct access without the
   workarounds or unofficial APIs."

Use `robin_stocks` for reading historical data from an account you own if you like. Don't build an
execution path on it.

### Path C — a different broker (strongest for a serious system)

If the goal is a real quantitative system rather than specifically a Robinhood system, the broker
choice should follow the API, and Robinhood is not the best API.

| Broker | API quality | Options | Leverage/margin | Best for |
|---|---|---|---|---|
| **Alpaca** | Excellent; clean REST, no gateway software, official [MCP server](https://github.com/alpacahq/alpaca-mcp-server) | **Level 3 multi-leg** — spreads, condors, straddles. Free paper accounts get L3 automatically; live requires approval | Reg-T margin | **Best default for this project** |
| **Interactive Brokers** | Most comprehensive in the industry; heavier setup (gateway) | Full, plus futures/global | Portfolio margin available | Serious multi-asset scale |
| **Tastytrade** | API-first, options-native | Full; $0 closing commissions matters if you trade frequently | Portfolio margin | Options-heavy strategies |
| **Tradier** | Simple, developer-friendly, options-focused | Full | Reg-T | Lightweight options automation |
| **Robinhood Agentic** | MCP-native, simplest possible setup | Rolling out | **None** | Agent experiments, small capital |
| ~~TD Ameritrade~~ | **Dead** — API permanently shut down; Schwab's successor is workable but not preferred for algo use | | | |

Alpaca's free paper-trading tier with automatic Level‑3 access is, concretely, the single most useful
fact in this table: you can develop and validate the entire options system at zero risk and zero cost
before a dollar is live.

**A note on the regulatory backdrop:** on April 14, 2026 the SEC approved FINRA's amendments to Rule
4210, **eliminating the $25,000 pattern-day-trader minimum and the PDT designation itself**, effective
June 4, 2026. Margin accounts above $2,000 now get intraday buying power set by the broker. Brokers have
until October 20, 2027 to implement, so availability varies. This removes a barrier that used to shape
retail bot design — but note it lowers the *floor*, it does not raise the *edge*.

---

## 3. Repositories worth your time

Sorted by what you'd actually use them for.

### Multi-agent LLM trading (directly relevant to your two-agent idea)

| Repo | Stars | What it is | Verdict |
|---|---|---|---|
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | ~100k | The reference implementation of your exact idea: fundamentals / sentiment / news / technical analysts → bull-vs-bear researcher debate → trader → risk manager. v0.3.1, Apache‑2.0, multi-provider LLM support. [Paper: arXiv:2412.20138](https://arxiv.org/pdf/2412.20138) | **Read this first.** Trades against a *simulated* exchange — research scaffold, not live execution. Its own README warns backtest results "are not guaranteed to match any published figure." |
| [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | ~32k | Newer (April 2026), MCP-native personal trading agent. 74+ MCP tools, 88 skills, **13 live brokers** (Alpaca, IBKR, Futu, MT5…), 25+ data sources, options analytics, plus mandate gates, exposure caps, audit ledgers and kill switches | **Closest to a production-shaped system.** The risk-control scaffolding is the valuable part. |
| [ginlix-ai/LangAlpha](https://github.com/ginlix-ai/LangAlpha) | ~1.7k | "Claude Code for financial markets" — LangGraph-based | Useful architectural reference |
| [mnemox-ai/tradememory-protocol](https://github.com/mnemox-ai/tradememory-protocol) | ~1.4k | Decision audit trail + outcome-weighted memory for trading agents; tamper-evident SHA‑256 chain | Solves a real problem: how an agent learns from its own past decisions without look-ahead contamination |

### Infrastructure you should not rebuild

| Repo | Stars | Use it for |
|---|---|---|
| [microsoft/qlib](https://github.com/microsoft/qlib) | ~48k | AI-oriented quant platform; supervised learning, RL, market-regime modeling. Industrial-grade |
| [stefan-jansen/machine-learning-for-trading](https://github.com/stefan-jansen/machine-learning-for-trading) | ~21k | The best end-to-end curriculum: data sourcing → live execution. 3rd edition |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | ~8.9k | Vectorized backtesting at scale — sweep thousands of parameterizations fast |
| [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | ~8.9k | Simple, readable backtesting for a first strategy |
| [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | ~7.6k | Tearsheets and portfolio analytics. **Non-optional** — this is how you'll know if you have anything |
| [hudson-and-thames/mlfinlab](https://github.com/hudson-and-thames/mlfinlab) | ~4.9k | Implements *Advances in Financial ML* — triple-barrier labeling, purged CV, meta-labeling. The correct way to avoid fooling yourself |
| [PyPortfolio/PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) | ~6.0k | Position sizing, Black-Litterman, hierarchical risk parity |
| [alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) | ~929 | Official Alpaca MCP — stocks, ETFs, crypto **and options**, in plain English from an LLM client |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | ~16k | Deep RL for trading. Impressive; see §6 on why RL backtests mislead badly |
| [paperswithbacktest/awesome-systematic-trading](https://github.com/paperswithbacktest/awesome-systematic-trading) | ~14k | Curated index when you need something specific |

### Robinhood-specific repos

Notably thin: [2018kguo/RobinhoodBot](https://github.com/2018kguo/RobinhoodBot) (~229 stars) and
[Jake0303/RobinHood-RSI-Trading-Bot](https://github.com/Jake0303/RobinHood-RSI-Trading-Bot) (~222).
Both are simple technical-indicator bots on `robin_stocks`. **That thinness is itself a finding.** In a
domain where the good repos have 20k–100k stars, the Robinhood-native ecosystem tops out in the low
hundreds — because the API was never sanctioned. The Agentic MCP server may change this; as of now,
there is no mature open-source Robinhood trading stack to inherit.

---

## 4. Options: yes, with real caveats

**Can you trade them?** Robinhood's product page lists options as supported through the MCP server,
though the rollout has been staged since the equities-only beta launch — confirm on your own account.
Alpaca supports full Level‑3 multi-leg today, with free paper access.

**Should you?** Options are the only way to get meaningful leverage in a Robinhood agentic account,
since margin borrowing isn't available there. That makes them structurally central to any high-return
plan — which is exactly why the evidence deserves a hard look:

- Roughly **73% of retail options traders lose money over any 12-month period**. Among traders using
  *defined-risk spread* strategies that drops to ~52% — a large, real, actionable difference.
- **0DTE options trades underperform other options trades by 4.7%** (t‑stat −10), while non‑0DTE trades
  earn +0.19%. Retail 0DTE losses run roughly $350k/day in aggregate.
- The asymmetry that should drive your design: retail **debit** 0DTE positions lose ~$8.05/contract
  while retail **credit** positions make ~$4.55/contract. Buying short-dated premium is where retail
  money goes to die; selling defined-risk premium is where the survivors live.
- Cause is mundane: 0DTE contracts are cheap, so the bid-ask spread is enormous *as a fraction of
  premium*. You pay the spread on entry and exit. An agent that trades "constantly" pays it constantly.

**Design implication.** If you trade options with this system, the evidence points at *defined-risk,
premium-selling, non‑0DTE* structures — credit spreads, iron condors, cash-secured puts, covered calls —
sized so that no single expiry can materially hurt you. That is the strategy family with the best
retail survival statistics. It is also, inconveniently, a strategy family that produces steady
mid-double-digit returns at best, not 300%.

Also budget for the operational reality: options are path-dependent and assignment-sensitive. An LLM
agent that misjudges a short leg near expiry can create an obligation it did not model. Multi-leg
orders (which Alpaca supports natively) matter here — legging into a spread manually exposes you to
execution risk between legs.

---

## 5. The secondary intelligence agent

This part of your plan is sound, common, and the most intellectually interesting piece. It's also where
the failure modes are subtlest.

### Architecture that works

The consensus pattern — TradingAgents, Vibe-Trading, and the 2026 literature all converge on it — is a
**pipeline with an explicit adversarial step and a hard risk gate**:

```
  ┌─ Fundamentals analyst ─┐
  ├─ News / macro analyst  ┤
  ├─ Sentiment analyst     ┼─→ Bull researcher ⇄ Bear researcher ─→ Trader ─→ Risk manager ─→ Broker
  ├─ Technical analyst     ┤        (structured debate)              (sizing)   (veto power)     (MCP)
  └─ Alt-data analyst ─────┘
```

Three design rules that separate the systems that survive from the ones that don't:

1. **The risk manager holds a veto, and it is deterministic code — not an LLM.** Position limits,
   exposure caps, drawdown kill-switches and per-trade maximums must be enforced by a program that
   cannot be argued with. LLMs are persuadable; that is their function. Vibe-Trading's "mandate gates"
   and "exposure caps" are the right shape.
2. **The intelligence agent proposes; it never executes.** One-directional data flow into the trader.
3. **Log every decision with its inputs, immutably and timestamped.** Without this you cannot later
   distinguish skill from luck, and you cannot detect look-ahead contamination.

### Data sources for the intelligence layer

| Category | Sources | Notes |
|---|---|---|
| News + sentiment | **Alpha Vantage News & Sentiment** (200k+ tickers, LLM-scored, native MCP server as of 2026), **Benzinga via Polygon.io** (~25ms WebSocket — trader-grade), Tiingo, Finnhub | Alpha Vantage for breadth; Benzinga/Polygon for latency |
| Congressional trading | **Quiver Quantitative** (api.quiverquant.com, >99% match accuracy vs. official filings), Unusual Whales (options flow + political), Capitol Trades | See the timing problem below |
| Insider transactions | SEC EDGAR **Form 4** — free, direct, **2-business-day** filing deadline | Far more timely than congressional data |
| Macro / policy | FRED (Federal Reserve), Treasury, Federal Register, Congress.gov | Free and underused |
| Prediction markets | Polymarket, Kalshi | Crowd-priced probabilities on political/policy events — a genuinely useful signal for a politics-aware agent |

### The honest read on political/insider signals

You specifically asked about politicians and government initiatives, so this deserves directness.

**The timing problem is severe.** Under the STOCK Act, members of Congress have **45 days** to disclose
a trade. Corporate insiders filing Form 4 have **two business days**. By the time you can read a
congressional trade, it is on average weeks old and already priced. Research cited by Meridian finds
~6% 30-day alpha versus the S&P *when filtered by committee assignment and trade size* — a real effect,
but one measured on a lagged, widely-watched, heavily-crowded signal that dozens of ETFs and thousands
of retail traders now trade the moment it publishes.

**And the supply may be about to shrink.** On July 22, 2026 the House passed a bill 232–198 barring
sitting members from buying individual stocks and requiring advance notice of sales. Its Senate
prospects are uncertain (voter‑ID provisions were attached). If some version passes, a data source you
architected around substantially dries up. Don't make it load-bearing.

**Where the intelligence layer genuinely earns its keep** is less glamorous than reading Congress:
scheduled-event awareness (earnings, FOMC, CPI dates), regime detection, correlation-shift monitoring,
and — most valuably — *veto power*. An agent that reliably tells your trader "there is an FOMC decision
in 90 minutes, do not open new short-vol exposure" adds more expected value than one hunting for alpha
in 45-day-old disclosures.

### The bias that will silently destroy your backtest

This is the most important technical warning in the report.

LLMs are trained on data through a cutoff date. If you backtest a news-reading agent on 2023 headlines
using a model trained through 2025, **the model already knows what happened next.** Your backtest is
not a test; it is a memory exam. Results will look spectacular and will not survive contact with live
markets.

[Look‑Ahead‑Bench (arXiv:2601.13770)](https://arxiv.org/pdf/2601.13770) formalizes this for point-in-time
financial LLMs and finds it materially inflates Sharpe ratios and returns across FinGPT, FinMem, and
agent-based trading systems. The AlphaCrafter authors take it seriously enough that they deliberately
place their live-trading evaluation window **strictly after every backbone model's training cutoff**
specifically "to eliminate confounding effects from LLM memory."

**Mitigations, in descending order of effectiveness:**
1. Evaluate only on data *after* your model's training cutoff. Nothing else is as reliable.
2. Forward paper-trade for months. It is slow, and it is the only ground truth you have.
3. Strict point-in-time data hygiene — no restated fundamentals, no survivorship-biased universes, no
   post-facto index membership.
4. Purged, embargoed cross-validation (see `mlfinlab`) for any ML component.

---

## 6. The 3–4x question, with numbers

I ran the simulations rather than asserting a conclusion. Both scripts are in this directory
(`risk_simulation.py`, `kelly_degradation.py`) — 200,000 Monte Carlo paths, 252 trading days, geometric
Brownian motion.

### What return level is even being asked for

3x is **+200%**; 4x is **+300%** in twelve months. For calibration:

- **Renaissance Technologies' Medallion Fund** — the most successful trading operation ever documented —
  averaged **~66% gross / ~39% net** annually over three decades, and has been closed to outside money
  since 1993.
- The best LLM trading agents in the 2026 literature returned **8–16% annualized in live evaluation**, topping out at 16.3% (Sharpe 1.60).
- You are asking for **5–8x Medallion's net return**, using retail infrastructure, an unproven strategy,
  and an account type that provides no leverage.

### The core tradeoff

For a strategy with annualized Sharpe *S* and volatility *σ*, expected return is *S·σ*. Sharpe is the
part that's hard to get; volatility is the part you can dial up freely with leverage. So the only way
to reach +200% is to crank volatility — and volatility cuts both ways.

Probability of reaching 3x within 12 months, against the probability of severe loss along the way:

| Sharpe | Ann. vol | E[return] | **P(≥3x)** | P(lose half) | P(lose 90%) | Median max drawdown | Median outcome |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 30% | 15% | 0.0% | 0.8% | 0.0% | −25% | 1.11x |
| 0.5 | 100% | 50% | 13.5% | 46.4% | 2.0% | −66% | 1.00x |
| 0.5 | 200% | 100% | 14.8% | **82.3%** | **39.6%** | −93% | **0.37x** |
| 1.0 | 60% | 60% | 12.9% | 8.8% | 0.0% | −40% | 1.52x |
| 1.0 | 150% | 150% | 31.3% | 53.8% | 7.7% | −77% | 1.45x |
| **1.5** | **100%** | **150%** | **46.0%** | **18.4%** | 0.1% | **−54%** | 2.71x |
| 1.5 | 150% | 225% | 50.6% | 39.2% | 3.0% | −71% | 3.07x |
| 2.0 | 100% | 200% | 65.5% | 10.0% | 0.0% | −49% | 4.48x |
| 3.0 | 100% | 300% | 91.9% | 2.6% | 0.0% | −42% | 12.10x |

Read the table this way: **3x is not gated on cleverness in strategy, it is gated on Sharpe ratio.**

- At Sharpe 0.5 — already better than most retail systems — **no amount of leverage gets P(3x) above
  ~16%**, and pushing for it makes the median outcome *losing money*. Leverage past the optimum
  destroys compounding rather than accelerating it.
- At Sharpe 1.5 — matching the *best* result in the 2026 agent literature — 3x becomes a coin flip,
  and the price of that coin flip is a median 54% drawdown and a ~1-in-5 chance of halving your capital.
- Sharpe 2.0+ sustained is hedge-fund-elite territory.

### The leverage you'd need doesn't exist in this account

A strategy running Sharpe 1.5 at a natural 10% volatility needs **15x leverage** to reach 150% vol.

| Sharpe | Full-Kelly leverage | Implied ann. vol | E[growth] at full Kelly | At half Kelly |
|---:|---:|---:|---:|---:|
| 0.5 | 5.0x | 50% | 13% | 10% |
| 1.0 | 10.0x | 100% | 65% | 45% |
| 1.5 | 15.0x | 150% | **208%** | 133% |
| 2.0 | 20.0x | 200% | 639% | 348% |

**"3x in a year" is almost exactly full-Kelly betting on a Sharpe‑1.5 strategy.** That is a precise
characterization, and it is damning, because full Kelly is universally regarded as too aggressive to
run in practice — it maximizes long-run growth while accepting drawdowns that no human tolerates and
that assume your edge estimate is *exactly right*. Practitioners run half Kelly or less.

Robinhood's agentic accounts provide **no borrowing power at all**. Reg-T margin elsewhere gives 2x,
portfolio margin maybe 6x. 15x is reachable only through options — which is why the plan inevitably
routes to options, and why §4's statistics matter so much.

### What happens when your edge is smaller than you think

This is the scenario that actually plays out. You size for a backtested Sharpe of 1.5; live performance
degrades — as every source in §7 says it does:

| True live Sharpe | P(≥3x) | **P(lose half)** | P(lose 90%) | Median outcome |
|---:|---:|---:|---:|---:|
| 1.50 (backtest holds) | 50.7% | 39.0% | 3.0% | 3.08x |
| 1.00 | 31.6% | 54.0% | 7.6% | 1.46x |
| **0.50** (typical degradation) | 16.3% | **69.3%** | 16.8% | **0.69x** |
| 0.25 | 10.9% | 76.2% | 23.4% | 0.47x |
| 0.00 (no edge, just leverage) | 6.9% | 82.2% | 31.1% | 0.33x |

A modest, entirely normal overestimate of edge — 1.5 becomes 0.5 — flips the median outcome from
*tripling* to *losing 31%*, with a **69% chance of halving your capital at some point during the year.**

And GBM is *generous* here. Real leveraged options positions have jump risk, gap risk, assignment risk,
and correlation that spikes exactly when you need it not to. The true tails are fatter than these
numbers.

---

## 7. What the evidence says about who succeeds

### Retail trading generally

- **Taiwan, 15 years, 360,000 day traders** (Barber & Odean): fewer than 1% were reliably profitable
  after fees. Day traders lost an average of 23.9 bps per day net.
- **Brazil** (Chague & De-Losso): of traders persisting 300+ days, **97% lost money**; under 1% earned
  more than the Brazilian minimum wage.
- **US** (Barber & Odean 2000): the most active quintile underperformed the market by ~6.5pp annually.
- Studies across 8 countries converge on **74–97% loss rates**, with 1–3% profitable over 3+ years.

The consistent finding across all of them is that **trading frequency is inversely related to returns.**
Your phrase "trades constantly" describes the single most reliable predictor of retail underperformance
in the literature. Automation doesn't exempt you from this — it removes the emotional errors while
*amplifying* the cost errors, because a bot pays spreads and fees with perfect discipline and infinite
stamina.

### AI/LLM trading systems specifically

The [AlphaCrafter paper (arXiv:2605.05580)](https://arxiv.org/abs/2605.05580) is the most useful thing
published on this. It benchmarks traditional methods, ML, and LLM agents across backtesting
(Jan 2024 – Feb 2026) and **live paper trading through a real brokerage** (Mar 2 – Jun 12, 2026), with
the live window placed deliberately after every model's training cutoff.

Annualized return (AR) and Sharpe (SR), backtest → live:

| Method | Backtest AR / SR (S&P 500) | **Live AR / SR (S&P 500)** | Live AR (CSI 300) |
|---|---|---|---|
| LSTM | 16.26% / 1.38 | **7.52% / 0.83** | 3.22% |
| XGBoost | 2.08% / −0.16 | 9.68% / 1.01 | 3.40% |
| Transformer | 7.22% / 0.44 | **5.09% / 0.28** | **−2.31%** |
| MACD (plain indicator) | 7.92% / 0.72 | 18.76% / 1.22 | 10.29% |
| TradingAgents (GPT‑5.3) | 10.75% / 0.98 | 10.45% / 1.12 | 7.72% |
| TradingAgents (Claude Opus 4.6) | 11.21% / 1.08 | 13.45% / 1.25 | 6.72% |
| TradingGroup (Claude Opus 4.6) | 10.58% / 0.98 | 8.32% / 0.92 | 3.52% |
| AlphaAgent (Claude Opus 4.6) | 14.51% / 1.27 | 15.75% / 1.58 | 11.22% |
| AlphaCrafter (GPT‑5.3) | 13.51% / 1.25 | 14.02% / 1.45 | 9.57% |
| **AlphaCrafter (Claude Opus 4.6)** | 15.66% / 1.34 | **16.26% / 1.60** | 10.70% |

Four conclusions worth internalizing:

1. **The best system in the published state of the art returns ~16% live at Sharpe ~1.6.** Not 200%.
2. **Deep learning collapses out-of-sample.** LSTM posted the best backtest and roughly halved live. The
   paper's own words: pronounced degradation "exposing their vulnerability to overfitting and regime
   shifts."
3. **A plain MACD indicator beat most of the AI systems on live S&P returns** (18.76%) — while carrying
   the worst drawdowns and a mediocre Sharpe, because it was just capturing market beta in a rising
   market. If your bot's "edge" is beta, you will discover it on the way down.
4. **Role-playing agent architectures are unstable.** TradingAgents and TradingGroup showed "considerable
   cross-model instability" — the same architecture on a different LLM gives materially different
   results. Structured, factor-centric workflows were far more reproducible. This is a direct argument
   for constraining your agents with explicit programmatic policy rather than free-form prompting.

Independent practitioner reports align: of 47 AI trading systems tested with real money, ~3% survived a
real drawdown.

**What does work,** consistently, across the successful cases: capturing structural risk premia
(volatility risk premium via defined-risk premium selling, carry, momentum), disciplined position
sizing well below Kelly, ruthless cost control, and diversification across many small uncorrelated
bets. Medallion's 66% didn't come from one brilliant call — it came from an enormous number of tiny,
slightly-better-than-random trades with world-class execution infrastructure you cannot replicate.

---

## 8. What I'd actually build

The goal reframed: **build a system that is honestly measured, structurally safe, and capable of
compounding.** If it turns out to have real edge, leverage is a decision you can make later from a
position of knowledge. If you leverage first, you never find out whether you had edge — you just find
out the market's opinion of your account balance.

### Target architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│ INTELLIGENCE LAYER (read-only; proposes, never executes)                 │
│   news+sentiment (Alpha Vantage / Benzinga) · macro calendar (FRED)      │
│   SEC Form 4 (2-day lag) · Quiver congressional (45-day lag) · Polymarket│
│                  ↓ structured signals + confidence + provenance          │
├──────────────────────────────────────────────────────────────────────────┤
│ STRATEGY LAYER (LLM reasoning, bounded)                                  │
│   analysts → bull/bear debate → trade proposals with explicit theses     │
├──────────────────────────────────────────────────────────────────────────┤
│ RISK GATE (deterministic code — NO LLM, NO override path)                │
│   max position size · max portfolio exposure · max daily loss            │
│   drawdown kill switch · trade-frequency cap · defined-risk-only         │
│   pre-trade cost estimate (reject if edge < 2× expected spread+fees)     │
├──────────────────────────────────────────────────────────────────────────┤
│ EXECUTION  → Alpaca (paper → live) and/or Robinhood Agentic MCP          │
├──────────────────────────────────────────────────────────────────────────┤
│ LEDGER  immutable, timestamped, every decision + every input + outcome   │
└──────────────────────────────────────────────────────────────────────────┘
```

The risk gate being non-LLM code with no override path is the most important line in this document.
Everything above it is a proposal engine. That layer is what stands between a bad week and a blown account.

### Staged plan

**Weeks 1–3 — Infrastructure, zero strategy.**
Alpaca paper account (free Level‑3 options). Wire the MCP server. Build the ledger and the risk gate
*first* — before any strategy exists, so you're never tempted to relax them to accommodate one.
Stand up `quantstats` reporting. Fork `TradingAgents` and read it end to end.

**Weeks 4–8 — Strategy, honestly evaluated.**
Pick one narrow, defensible thesis. Given §4, the strongest starting candidate is **defined-risk
premium selling on liquid large-cap underlyings, 20–45 DTE, avoiding earnings** — harvesting the
volatility risk premium, which is a documented structural premium rather than a pattern you found in
noise. Backtest with purged CV (`mlfinlab`). Model costs pessimistically — double the spread you think
you'll pay.

**Weeks 9–20 — Paper trade. Do not skip this.**
Minimum three months live paper trading. This is the only defense against look-ahead bias that actually
works. Measure Sharpe, max drawdown, hit rate, cost drag. **Decision gate: if live-paper Sharpe < 0.5,
you do not have a strategy** — go back to week 4. Most ideas die here, and that is the system working.

**Weeks 21+ — Small real capital.**
Fund an amount whose total loss would be genuinely irrelevant to you. Robinhood's agentic account is
well-suited here precisely because it's walled off and unleveraged. Size at **quarter to half Kelly at
most** on your *live-paper* Sharpe estimate, never the backtest number. Scale only on live results.

### Realistic expectations

| Outcome | Probability | Note |
|---|---|---|
| Strategy dies in paper trading | ~60–70% | The normal, healthy outcome |
| Live Sharpe 0.3–0.8, returns 5–20%/yr | ~25% | **This is success.** It beats most retail and most funds |
| Live Sharpe > 1.5 sustained | <5% | Publishable-quality result |
| 3–4x in 12 months without a ≥50% drawdown risk | **≪1%** | Requires an edge nobody has demonstrated at retail scale |

If the system is genuinely good — say a live Sharpe of 1.0 at sane leverage — you're looking at roughly
20–45% a year. Compounded, that is a *life-changing* outcome over five to ten years, and it is a real
thing to aim at. The 3–4x target isn't a more ambitious version of that goal; it's a different activity
with a different expected value, and the math in §6 says that expected value is negative once your edge
estimate is off by an ordinary amount.

---

## 9. Risks and obligations to plan for now

- **Tax.** Frequent trading generates short-term capital gains at ordinary income rates, plus wash-sale
  complications that an automated system will trigger constantly across similar positions. Options have
  their own treatment rules. A bot can generate thousands of taxable lots. Budget for a CPA who has seen
  algorithmic trading before, and export the ledger in a form they can use.
- **Prompt injection is a live attack surface.** Your intelligence agent reads untrusted text from the
  open internet, and it is connected — however indirectly — to something that places orders. Treat all
  fetched news and social content as hostile input. The risk gate must sit between the reasoning layer
  and the broker precisely so that a poisoned headline cannot become an order.
- **Robinhood disclaims everything.** It "does not control, supervise, monitor, recommend, or audit"
  agents, and you "assume all risk for orders placed by your AI agent." There is no recourse for a
  hallucinated trade.
- **Beta software, real money.** Agentic Trading launched three months ago. Expect bugs, changed tool
  surfaces, and behavior that shifts under you.
- **Operational continuity.** Desktop-hosted agents stop when the laptop sleeps — with positions open.
  If you're serious, host the loop on a server with monitoring and a heartbeat alert.
- **A kill switch you have tested.** Not a plan for one. Test it, in paper, under load.
- **Model dependency.** Agent performance varies materially by backbone LLM. A model upgrade can silently
  change your strategy's behavior. Pin versions and re-validate on every change.

---

## 10. Direct answers to your four questions

**"Can the bot talk directly to my Robinhood account?"**
Yes — officially, since May 2026, via Robinhood's Agentic Trading MCP server at
`https://agent.robinhood.com/mcp/trading`. Use it rather than `robin_stocks`. Note it provides no
leverage and, in a desktop client setup, only runs while your machine is awake.

**"Can we trade options?"**
Yes. Robinhood lists options support (verify on your account — the rollout has been staged); Alpaca
offers full Level‑3 multi-leg with free paper access. Strongly prefer *defined-risk* structures at
20–45 DTE. Avoid 0DTE — it's the worst-performing category in the retail data by a wide margin.

**"Can a second agent analyze news, politics, speeches and government initiatives?"**
Yes, and `TradingAgents` plus `Vibe-Trading` are working reference implementations. Build it read-only,
proposing into a deterministic risk gate. Weight timely sources (Form 4 at 2 days, real-time news,
prediction markets) far above lagging ones (congressional disclosures at 45 days, possibly restricted
by pending legislation). And guard obsessively against look-ahead bias — an LLM backtested on data
inside its training window is remembering, not predicting.

**"Can I 3–4x in 12 months?"**
Not with an acceptable risk of ruin, and not from anything demonstrated in the public state of the art.
3x/year is mathematically equivalent to full-Kelly betting on a Sharpe‑1.5 strategy; the best published
LLM trading agents achieve Sharpe ~1.5 at *unlevered* returns of 14–16%, and if your true edge is a
third of what you estimated — the normal case — you face a 69% chance of losing half your capital and a
median outcome of −31%.

The version of this worth building targets 15–40% annually with controlled drawdowns, measures itself
honestly, and compounds. That path can genuinely change your financial position over several years. It
just won't do it by June.

---

## Sources

**Robinhood / brokers**
- [Robinhood is Now Open to Agents](https://robinhood.com/us/en/newsroom/robinhood-is-now-open-to-agents/) · [Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) · [Agentic Trading product page](https://robinhood.com/us/en/agentic-trading/) · [Trading with your agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
- [Robinhood Crypto Trading API](https://robinhood.com/us/en/newsroom/robinhood-crypto-trading-api/) · [TechCrunch: Robinhood now lets your AI agents trade stocks](https://techcrunch.com/2026/05/27/robinhood-now-lets-your-ai-agents-trade-stocks/) · [Genfinity: Agentic Trading opens to crypto](https://genfinity.io/2026/07/21/robinhood-agentic-trading-crypto-ai-agents/) · [Finder: Agentic accounts review](https://www.finder.com/stock-trading/robinhood-agentic-accounts)
- [How to Build an AI Trading Agent on Robinhood (with Claude)](https://ryandoser.com/ai-trading-agent-robinhood/)
- [Alpaca: Multi-leg (Level 3) options](https://alpaca.markets/blog/level-3-options-trading-now-available-with-alpacas-trading-api/) · [Alpaca options](https://alpaca.markets/options) · [Best brokers for algo trading 2026](https://brokerchooser.com/best-brokers/best-brokers-for-algo-trading-in-the-united-states) · [Best API brokers 2026](https://investingintheweb.com/brokers/best-api-brokers/)

**Regulation**
- [SEC approves scrapping the $25,000 day-trader minimum](https://www.schwab.com/learn/story/sec-approves-scrapping-25000-day-trader-minimum) · [SEC filing SR-FINRA-2025-017](https://www.sec.gov/files/rules/sro/finra/2026/34-105226.pdf) · [PDT rule change for options traders](https://optionalpha.com/blog/pdt-rule-change-what-it-means-for-options-traders)
- [CNN: House passes bill to restrict lawmaker stock trading](https://www.cnn.com/2026/07/22/politics/stock-trading-restriction-congress) · [CRS R48641: Proposals to limit Member financial activities](https://www.congress.gov/crs-product/R48641) · [S.1879 Ban Congressional Stock Trading Act](https://www.congress.gov/bill/119th-congress/senate-bill/1879)

**Research**
- [AlphaCrafter: Multi-Agent Workflows for Cross-Sectional Quantitative Trading (arXiv:2605.05580)](https://arxiv.org/abs/2605.05580) — backtest vs. live comparison table
- [Look-Ahead-Bench: Look-ahead Bias in Point-in-Time LLMs for Finance (arXiv:2601.13770)](https://arxiv.org/pdf/2601.13770)
- [TradingAgents: Multi-Agents LLM Financial Trading Framework (arXiv:2412.20138)](https://arxiv.org/pdf/2412.20138)
- [Beckmeyer, Branger & Gayda: Retail Traders Love 0DTE Options… But Should They? (SSRN)](https://papers.ssrn.com/sol3/Delivery.cfm/4404704.pdf?abstractid=4404704&mirid=1) · [Bogousslavsky & Muravyev: An Anatomy of Retail Option Trading](https://www.lsu.edu/business/files/event-files/2025-finance-mardi-gras/retail_option_trading_v2.pdf) · [Cboe: New Evidence on the Performance of Customer Options Trades](https://cdn.cboe.com/resources/education/research_publications/Retail_Profitability.pdf)
- [Barber & Odean: Day Traders Lose Money and Keep Trading (Taiwan)](https://www.tradicted.com/research/barber-learning-2020/) · [Day trading failure rate: 30 studies, 8 countries](https://bananafarmer.app/research/day-trading-failure-rate)
- [Medallion Fund returns](https://www.quantifiedstrategies.com/medallion-fund-returns/)

**Data providers**
- [Alpha Vantage News & Sentiment](https://www.alphavantage.co/best_stock_market_api_review/) · [Best financial news sentiment APIs 2026](https://adanos.org/insights/blog/best-financial-news-sentiment-apis-2026/) · [Quiver Quantitative Congress Trading](https://www.quiverquant.com/congresstrading/) · [Congress tracker comparison 2026](https://meridianfin.io/knowledge/congress-tracker-comparison-2026)

---

*Research compiled with Claude Code. Not financial advice. All simulations in this directory are
reproducible: `python3 risk_simulation.py` and `python3 kelly_degradation.py` (requires numpy).*
