"""The risk gate.

Everything above this layer is a proposal engine. This module is the only thing
standing between a bad week and a blown account, so it holds to three rules:

  1. No LLM call happens in here, ever.
  2. There is no override path. A rejected proposal is rejected.
  3. Every check is independently testable and every rejection is logged.

Checks run in a fixed order and ALL of them run: we collect every reason a
proposal fails rather than short-circuiting, because the rejection log is a
primary input to the nightly review.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Sequence


class Verdict(str, Enum):
    ALLOW = "allow"
    REJECT = "reject"


# Rejection codes are stable identifiers, not prose: the review job groups on
# them, so renaming one silently breaks the agent's ability to learn from it.
class Reason(str, Enum):
    KILL_SWITCH = "kill_switch_active"
    MARKET_CLOSED = "market_closed"
    ENTRY_BLACKOUT = "entry_blackout_window"
    POSITION_TOO_LARGE = "position_exceeds_max_notional"
    SYMBOL_CONCENTRATION = "symbol_exposure_exceeded"
    PORTFOLIO_DEPLOYED = "portfolio_deployment_exceeded"
    DAILY_LOSS_LIMIT = "daily_loss_limit_hit"
    DRAWDOWN_LIMIT = "drawdown_kill_switch"
    TRADE_COUNT_DAY = "max_trades_per_day"
    TRADE_COUNT_CYCLE = "max_trades_per_cycle"
    COST_EXCEEDS_EDGE = "estimated_cost_exceeds_edge"
    SPREAD_TOO_WIDE = "spread_too_wide"
    UNDEFINED_RISK = "undefined_risk_structure"
    DTE_TOO_SHORT = "dte_below_minimum"
    INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"
    UNSETTLED_FUNDS = "unsettled_funds"
    SETUP_BLOCKED = "setup_blocked_by_expectancy"
    CHURN = "same_symbol_reentry"
    NOT_IN_UNIVERSE = "symbol_not_in_universe"
    STALE_DATA = "market_data_stale"


@dataclass(frozen=True)
class RiskLimits:
    """Hard limits. Loaded from config; never mutated at runtime."""

    max_position_pct: float = 0.02          # of equity, per position
    max_symbol_pct: float = 0.05            # of equity, aggregate per symbol
    max_deployed_pct: float = 0.40          # of equity, total at risk
    daily_loss_limit_pct: float = 0.03      # of start-of-day equity
    max_drawdown_pct: float = 0.15          # peak-to-trough, trips kill switch
    max_trades_per_day: int = 8
    max_trades_per_cycle: int = 2
    min_edge_cost_multiple: float = 2.0     # expected edge must be >= N x cost
    max_spread_pct: float = 0.05            # of mid price
    min_dte: int = 20                       # options: no 0DTE, per the research
    require_defined_risk: bool = True
    entry_blackout_minutes_open: int = 15   # no entries in first N min
    entry_blackout_minutes_close: int = 20  # or last N min
    max_data_age_seconds: int = 120
    allow_reentry_same_day: bool = False


@dataclass(frozen=True)
class Proposal:
    symbol: str
    asset_class: str                # 'equity' | 'option'
    side: str
    quantity: float
    notional: float                 # absolute dollars committed
    max_loss: float                 # worst case dollars; == notional for equity long
    setup_tag: str
    confidence: float               # agent's stated P(win)
    thesis: str
    expected_edge_pct: float        # agent's expected return on capital at risk
    est_spread_pct: float           # (ask-bid)/mid at decision time
    est_fees: float = 0.0
    dte: int | None = None          # options only
    defined_risk: bool = True
    is_closing: bool = False        # exits are held to a much looser standard
    data_age_seconds: float = 0.0


@dataclass(frozen=True)
class AccountState:
    equity: float
    cash: float
    buying_power: float
    settled_cash: float
    start_of_day_equity: float
    peak_equity: float
    realized_pnl_today: float
    unrealized_pnl_today: float
    deployed_notional: float
    symbol_exposure: dict[str, float] = field(default_factory=dict)
    trades_today: int = 0
    trades_this_cycle: int = 0
    symbols_closed_today: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MarketContext:
    now: datetime
    is_open: bool
    minutes_since_open: float
    minutes_until_close: float
    blocked_setups: frozenset[str] = frozenset()
    universe: frozenset[str] | None = None      # None = no universe restriction
    kill_switch: bool = False


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    reasons: tuple[Reason, ...]
    details: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


def evaluate(
    proposal: Proposal,
    account: AccountState,
    market: MarketContext,
    limits: RiskLimits,
) -> GateResult:
    """Run every check. Returns all failures, not just the first."""
    reasons: list[Reason] = []
    details: list[str] = []

    def fail(reason: Reason, detail: str) -> None:
        reasons.append(reason)
        details.append(detail)

    # --- Checks that apply to closing orders too -------------------------
    # We never block an exit for concentration or frequency reasons: refusing to
    # let the agent reduce risk is strictly worse than letting it.
    if market.kill_switch and not proposal.is_closing:
        fail(Reason.KILL_SWITCH, "kill switch is engaged; only closing orders allowed")

    if not market.is_open:
        fail(Reason.MARKET_CLOSED, f"market closed at {market.now.isoformat()}")

    if proposal.data_age_seconds > limits.max_data_age_seconds:
        fail(
            Reason.STALE_DATA,
            f"quote is {proposal.data_age_seconds:.0f}s old "
            f"(limit {limits.max_data_age_seconds}s)",
        )

    if proposal.is_closing:
        # Exits stop here. An exit only needs an open market and fresh data.
        return _result(reasons, details)

    # --- Drawdown and daily loss ------------------------------------------
    drawdown = _drawdown_pct(account.equity, account.peak_equity)
    if drawdown >= limits.max_drawdown_pct:
        fail(
            Reason.DRAWDOWN_LIMIT,
            f"drawdown {drawdown:.1%} >= limit {limits.max_drawdown_pct:.1%}",
        )

    day_pnl = account.realized_pnl_today + account.unrealized_pnl_today
    if account.start_of_day_equity > 0:
        day_loss_pct = -day_pnl / account.start_of_day_equity
        if day_loss_pct >= limits.daily_loss_limit_pct:
            fail(
                Reason.DAILY_LOSS_LIMIT,
                f"down {day_loss_pct:.2%} today >= limit "
                f"{limits.daily_loss_limit_pct:.2%}",
            )

    # --- Frequency. The research is unambiguous that trade count is the ----
    # --- most reliable predictor of retail underperformance. --------------
    if account.trades_today >= limits.max_trades_per_day:
        fail(
            Reason.TRADE_COUNT_DAY,
            f"{account.trades_today} trades today >= limit {limits.max_trades_per_day}",
        )
    if account.trades_this_cycle >= limits.max_trades_per_cycle:
        fail(
            Reason.TRADE_COUNT_CYCLE,
            f"{account.trades_this_cycle} trades this cycle >= "
            f"limit {limits.max_trades_per_cycle}",
        )

    # --- Entry timing ------------------------------------------------------
    if market.minutes_since_open < limits.entry_blackout_minutes_open:
        fail(
            Reason.ENTRY_BLACKOUT,
            f"{market.minutes_since_open:.0f}min after open < "
            f"{limits.entry_blackout_minutes_open}min blackout",
        )
    if market.minutes_until_close < limits.entry_blackout_minutes_close:
        fail(
            Reason.ENTRY_BLACKOUT,
            f"{market.minutes_until_close:.0f}min to close < "
            f"{limits.entry_blackout_minutes_close}min blackout",
        )

    # --- Universe and setup gating ----------------------------------------
    if market.universe is not None and proposal.symbol not in market.universe:
        fail(Reason.NOT_IN_UNIVERSE, f"{proposal.symbol} not in configured universe")

    if proposal.setup_tag in market.blocked_setups:
        fail(
            Reason.SETUP_BLOCKED,
            f"setup '{proposal.setup_tag}' blocked by realized expectancy",
        )

    if not limits.allow_reentry_same_day and proposal.symbol in account.symbols_closed_today:
        fail(Reason.CHURN, f"{proposal.symbol} already closed today; no re-entry")

    # --- Sizing ------------------------------------------------------------
    # Sizing is measured on MAX LOSS, not notional. For a defined-risk spread
    # those differ by an order of magnitude, and notional-based sizing would
    # either block every spread or wildly undersize equity.
    if account.equity > 0:
        position_pct = proposal.max_loss / account.equity
        if position_pct > limits.max_position_pct:
            fail(
                Reason.POSITION_TOO_LARGE,
                f"max loss {position_pct:.2%} of equity > "
                f"limit {limits.max_position_pct:.2%}",
            )

        existing = account.symbol_exposure.get(proposal.symbol, 0.0)
        symbol_pct = (existing + proposal.max_loss) / account.equity
        if symbol_pct > limits.max_symbol_pct:
            fail(
                Reason.SYMBOL_CONCENTRATION,
                f"{proposal.symbol} exposure would be {symbol_pct:.2%} > "
                f"limit {limits.max_symbol_pct:.2%}",
            )

        deployed_pct = (account.deployed_notional + proposal.max_loss) / account.equity
        if deployed_pct > limits.max_deployed_pct:
            fail(
                Reason.PORTFOLIO_DEPLOYED,
                f"deployment would be {deployed_pct:.2%} > "
                f"limit {limits.max_deployed_pct:.2%}",
            )

    # --- Buying power and settlement --------------------------------------
    if proposal.notional > account.buying_power:
        fail(
            Reason.INSUFFICIENT_BUYING_POWER,
            f"need ${proposal.notional:,.2f}, have ${account.buying_power:,.2f}",
        )
    if proposal.notional > account.settled_cash:
        # Robinhood agentic accounts have no margin borrowing, so unsettled
        # funds are a real constraint rather than a formality.
        fail(
            Reason.UNSETTLED_FUNDS,
            f"need ${proposal.notional:,.2f} settled, "
            f"have ${account.settled_cash:,.2f}",
        )

    # --- Cost vs. edge -----------------------------------------------------
    # The single most common way an automated system bleeds out: paying more in
    # spread than the strategy can plausibly earn. An agent that trades every
    # 15 minutes pays this toll every 15 minutes.
    if proposal.est_spread_pct > limits.max_spread_pct:
        fail(
            Reason.SPREAD_TOO_WIDE,
            f"spread {proposal.est_spread_pct:.2%} > limit {limits.max_spread_pct:.2%}",
        )

    round_trip_cost_pct = _round_trip_cost_pct(proposal)
    required = round_trip_cost_pct * limits.min_edge_cost_multiple
    if proposal.expected_edge_pct < required:
        fail(
            Reason.COST_EXCEEDS_EDGE,
            f"edge {proposal.expected_edge_pct:.2%} < "
            f"{limits.min_edge_cost_multiple:g}x round-trip cost {round_trip_cost_pct:.2%}",
        )

    # --- Options-specific --------------------------------------------------
    if proposal.asset_class == "option":
        if limits.require_defined_risk and not proposal.defined_risk:
            fail(Reason.UNDEFINED_RISK, "undefined-risk options structure refused")
        if proposal.dte is not None and proposal.dte < limits.min_dte:
            fail(
                Reason.DTE_TOO_SHORT,
                f"{proposal.dte}DTE < minimum {limits.min_dte}DTE",
            )

    return _result(reasons, details)


def _result(reasons: list[Reason], details: list[str]) -> GateResult:
    verdict = Verdict.REJECT if reasons else Verdict.ALLOW
    return GateResult(verdict=verdict, reasons=tuple(reasons), details=tuple(details))


def _drawdown_pct(equity: float, peak: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak)


def _round_trip_cost_pct(p: Proposal) -> float:
    """Cost of getting in and out, as a fraction of capital at risk.

    Crossing the spread costs roughly half of it per side under normal
    conditions, so a round trip is about one full spread. We deliberately do not
    assume mid-price fills: an agent that assumes it always gets mid will
    systematically overestimate its edge.
    """
    base = max(p.max_loss, 1e-9)
    fee_pct = p.est_fees / base
    return p.est_spread_pct + fee_pct


@dataclass(frozen=True)
class ActionCheck:
    can_act: bool
    reason: str


def can_anything_happen(
    account: AccountState,
    market: MarketContext,
    limits: RiskLimits,
    has_open_positions: bool,
    min_viable_notional: float,
) -> ActionCheck:
    """Could ANY order pass the gate right now?

    A pure precondition check that runs before the model is called. On a
    20-minute cadence the LLM call is the dominant running cost, and a large
    share of cycles are provably incapable of producing a trade: the daily cap
    is spent, the account is flat with no settled cash, we are inside a blackout
    window. Paying for reasoning in those cycles buys nothing.

    Deliberately conservative in one direction: if any position is open, we
    always run, because exits must never be delayed to save money. This only
    ever skips cycles where both entry and exit are impossible.
    """
    if has_open_positions:
        # An exit may be needed, and exits bypass most limits.
        return ActionCheck(True, "positions open")

    if market.kill_switch:
        return ActionCheck(False, "kill switch engaged and no positions to close")

    if not market.is_open:
        return ActionCheck(False, "market closed")

    if market.minutes_since_open < limits.entry_blackout_minutes_open:
        return ActionCheck(False, "inside opening blackout, nothing to close")

    if market.minutes_until_close < limits.entry_blackout_minutes_close:
        return ActionCheck(False, "inside closing blackout, nothing to close")

    if account.trades_today >= limits.max_trades_per_day:
        return ActionCheck(False, "daily trade cap reached and no positions open")

    drawdown = _drawdown_pct(account.equity, account.peak_equity)
    if drawdown >= limits.max_drawdown_pct:
        return ActionCheck(False, "drawdown limit breached")

    if account.start_of_day_equity > 0:
        day_pnl = account.realized_pnl_today + account.unrealized_pnl_today
        if -day_pnl / account.start_of_day_equity >= limits.daily_loss_limit_pct:
            return ActionCheck(False, "daily loss limit reached")

    if account.settled_cash < min_viable_notional:
        return ActionCheck(
            False,
            f"settled cash ${account.settled_cash:,.2f} below the smallest "
            f"viable position (${min_viable_notional:,.2f})",
        )

    return ActionCheck(True, "")


def size_for_risk(
    equity: float,
    risk_per_trade_pct: float,
    max_loss_per_unit: float,
) -> int:
    """Units affordable at a given risk budget. Floors to zero, never negative."""
    if equity <= 0 or max_loss_per_unit <= 0 or risk_per_trade_pct <= 0:
        return 0
    budget = equity * risk_per_trade_pct
    return max(0, math.floor(budget / max_loss_per_unit))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
