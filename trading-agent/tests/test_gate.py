"""Risk gate tests.

The gate is the only component that cannot be allowed to have a bug, so these
tests are adversarial: each one tries to sneak a proposal past a limit.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tagent.risk.gate import (  # noqa: E402
    AccountState, GateResult, MarketContext, Proposal, Reason, RiskLimits,
    Verdict, evaluate, size_for_risk,
)

NOW = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)  # 11:00 ET, mid-session


def account(**over) -> AccountState:
    base = dict(
        equity=100_000.0, cash=60_000.0, buying_power=60_000.0,
        settled_cash=60_000.0, start_of_day_equity=100_000.0,
        peak_equity=100_000.0, realized_pnl_today=0.0, unrealized_pnl_today=0.0,
        deployed_notional=0.0, symbol_exposure={}, trades_today=0,
        trades_this_cycle=0, symbols_closed_today=frozenset(),
    )
    base.update(over)
    return AccountState(**base)


def market(**over) -> MarketContext:
    base = dict(
        now=NOW, is_open=True, minutes_since_open=90.0, minutes_until_close=300.0,
        blocked_setups=frozenset(), universe=None, kill_switch=False,
    )
    base.update(over)
    return MarketContext(**base)


def proposal(**over) -> Proposal:
    base = dict(
        symbol="AAPL", asset_class="equity", side="buy", quantity=10,
        notional=2_000.0, max_loss=1_000.0, setup_tag="mean_reversion",
        confidence=0.6, thesis="oversold bounce", expected_edge_pct=0.05,
        est_spread_pct=0.001, est_fees=1.0, dte=None, defined_risk=True,
        is_closing=False, data_age_seconds=5.0,
    )
    base.update(over)
    return Proposal(**base)


LIMITS = RiskLimits()


def check(p=None, a=None, m=None, limits=LIMITS) -> GateResult:
    return evaluate(p or proposal(), a or account(), m or market(), limits)


# --------------------------------------------------------------- happy path --

def test_clean_proposal_is_allowed():
    r = check()
    assert r.verdict is Verdict.ALLOW, r.details
    assert r.reasons == ()


# ------------------------------------------------------------- kill switch --

def test_kill_switch_blocks_new_entries():
    r = check(m=market(kill_switch=True))
    assert Reason.KILL_SWITCH in r.reasons


def test_kill_switch_still_permits_exits():
    """Refusing to let the agent reduce risk is worse than letting it."""
    r = check(p=proposal(is_closing=True), m=market(kill_switch=True))
    assert r.allowed, r.details


def test_closing_order_bypasses_sizing_and_frequency_limits():
    r = check(
        p=proposal(is_closing=True, max_loss=99_000.0, notional=99_000.0),
        a=account(trades_today=99, trades_this_cycle=99),
    )
    assert r.allowed, r.details


def test_closing_order_still_requires_open_market():
    r = check(p=proposal(is_closing=True), m=market(is_open=False))
    assert Reason.MARKET_CLOSED in r.reasons


# ------------------------------------------------------------------ sizing --

def test_position_size_measured_on_max_loss_not_notional():
    """A defined-risk spread has large notional but small max loss.

    Sizing on notional would block every spread; this asserts we do not.
    """
    r = check(p=proposal(notional=50_000.0, max_loss=1_500.0))
    assert Reason.POSITION_TOO_LARGE not in r.reasons


def test_oversized_position_rejected():
    r = check(p=proposal(max_loss=5_000.0))  # 5% of equity vs 2% limit
    assert Reason.POSITION_TOO_LARGE in r.reasons


def test_symbol_concentration_accumulates_existing_exposure():
    r = check(
        p=proposal(max_loss=1_900.0),
        a=account(symbol_exposure={"AAPL": 4_000.0}),
    )
    assert Reason.SYMBOL_CONCENTRATION in r.reasons


def test_portfolio_deployment_ceiling():
    r = check(a=account(deployed_notional=39_500.0))
    assert Reason.PORTFOLIO_DEPLOYED in r.reasons


# ------------------------------------------------------- loss and drawdown --

def test_daily_loss_limit():
    r = check(a=account(realized_pnl_today=-3_100.0))
    assert Reason.DAILY_LOSS_LIMIT in r.reasons


def test_daily_loss_counts_unrealized():
    """Unrealized losses are real losses; excluding them defeats the limit."""
    r = check(a=account(realized_pnl_today=-1_000.0, unrealized_pnl_today=-2_200.0))
    assert Reason.DAILY_LOSS_LIMIT in r.reasons


def test_drawdown_kill_switch():
    r = check(a=account(equity=84_000.0, peak_equity=100_000.0))
    assert Reason.DRAWDOWN_LIMIT in r.reasons


def test_drawdown_measured_against_peak_not_start_of_day():
    r = check(a=account(equity=88_000.0, peak_equity=120_000.0, start_of_day_equity=88_000.0))
    assert Reason.DRAWDOWN_LIMIT in r.reasons


# ---------------------------------------------------------------- frequency --

def test_daily_trade_cap():
    r = check(a=account(trades_today=8))
    assert Reason.TRADE_COUNT_DAY in r.reasons


def test_per_cycle_trade_cap():
    r = check(a=account(trades_this_cycle=2))
    assert Reason.TRADE_COUNT_CYCLE in r.reasons


def test_no_reentry_same_symbol_same_day():
    r = check(a=account(symbols_closed_today=frozenset({"AAPL"})))
    assert Reason.CHURN in r.reasons


# ------------------------------------------------------------ entry timing --

def test_opening_blackout():
    r = check(m=market(minutes_since_open=5.0))
    assert Reason.ENTRY_BLACKOUT in r.reasons


def test_closing_blackout():
    r = check(m=market(minutes_until_close=10.0))
    assert Reason.ENTRY_BLACKOUT in r.reasons


# ------------------------------------------------------------ cost vs edge --

def test_edge_must_exceed_cost_multiple():
    """2% spread needs >=4% expected edge at the default 2x multiple."""
    r = check(p=proposal(est_spread_pct=0.02, expected_edge_pct=0.03, est_fees=0.0))
    assert Reason.COST_EXCEEDS_EDGE in r.reasons


def test_sufficient_edge_passes():
    r = check(p=proposal(est_spread_pct=0.02, expected_edge_pct=0.05, est_fees=0.0))
    assert Reason.COST_EXCEEDS_EDGE not in r.reasons


def test_fees_count_toward_cost():
    """Same spread and edge, but fees large relative to capital at risk."""
    r = check(p=proposal(
        est_spread_pct=0.01, expected_edge_pct=0.025, est_fees=100.0, max_loss=1_000.0
    ))
    assert Reason.COST_EXCEEDS_EDGE in r.reasons


def test_wide_spread_rejected_outright():
    r = check(p=proposal(est_spread_pct=0.09, expected_edge_pct=0.90))
    assert Reason.SPREAD_TOO_WIDE in r.reasons


# --------------------------------------------------------------- options ---

def test_zero_dte_rejected():
    r = check(p=proposal(asset_class="option", dte=0))
    assert Reason.DTE_TOO_SHORT in r.reasons


def test_short_dte_rejected():
    r = check(p=proposal(asset_class="option", dte=7))
    assert Reason.DTE_TOO_SHORT in r.reasons


def test_compliant_dte_allowed():
    r = check(p=proposal(asset_class="option", dte=35))
    assert r.allowed, r.details


def test_undefined_risk_refused():
    r = check(p=proposal(asset_class="option", dte=35, defined_risk=False))
    assert Reason.UNDEFINED_RISK in r.reasons


def test_dte_limit_does_not_apply_to_equities():
    r = check(p=proposal(asset_class="equity", dte=None))
    assert Reason.DTE_TOO_SHORT not in r.reasons


# ----------------------------------------------------- capital and hygiene --

def test_insufficient_buying_power():
    r = check(p=proposal(notional=70_000.0), a=account(buying_power=60_000.0))
    assert Reason.INSUFFICIENT_BUYING_POWER in r.reasons


def test_unsettled_funds_blocked():
    """Agentic accounts have no margin borrowing, so settlement is a real gate."""
    r = check(p=proposal(notional=5_000.0), a=account(settled_cash=1_000.0))
    assert Reason.UNSETTLED_FUNDS in r.reasons


def test_stale_quote_rejected():
    r = check(p=proposal(data_age_seconds=600.0))
    assert Reason.STALE_DATA in r.reasons


def test_blocked_setup_rejected():
    r = check(m=market(blocked_setups=frozenset({"mean_reversion"})))
    assert Reason.SETUP_BLOCKED in r.reasons


def test_universe_restriction():
    r = check(m=market(universe=frozenset({"SPY", "QQQ"})))
    assert Reason.NOT_IN_UNIVERSE in r.reasons


# ------------------------------------------------------------- reporting ---

def test_all_failures_reported_not_just_first():
    """The rejection log feeds the nightly review, so it must be complete."""
    r = check(
        p=proposal(max_loss=50_000.0, est_spread_pct=0.5, expected_edge_pct=0.0),
        a=account(trades_today=99),
        m=market(minutes_since_open=1.0),
    )
    assert len(r.reasons) >= 4
    assert len(r.details) == len(r.reasons)


def test_no_override_path_exists():
    """GateResult is frozen: a caller cannot flip a rejection into an allow."""
    r = check(p=proposal(max_loss=99_000.0))
    assert not r.allowed
    with pytest.raises((AttributeError, TypeError)):
        r.verdict = Verdict.ALLOW  # type: ignore[misc]


# ---------------------------------------------------------------- sizing ---

@pytest.mark.parametrize(
    "equity,risk_pct,per_unit,expected",
    [
        (100_000, 0.02, 500, 4),
        (100_000, 0.02, 3_000, 0),   # cannot afford even one
        (0, 0.02, 500, 0),
        (100_000, 0.0, 500, 0),
        (100_000, 0.02, 0, 0),       # no divide-by-zero
        (100_000, 0.02, -5, 0),      # no negative sizing
    ],
)
def test_size_for_risk(equity, risk_pct, per_unit, expected):
    assert size_for_risk(equity, risk_pct, per_unit) == expected


# ------------------------------------------------- pre-check (cost control) --
# The model call is the dominant running cost. These assert we skip it only when
# no order could pass anyway - and NEVER when an exit might be needed.

from tagent.risk.gate import can_anything_happen  # noqa: E402


def precheck(a=None, m=None, positions=False, min_notional=1.0, limits=LIMITS):
    return can_anything_happen(a or account(), m or market(), limits,
                               has_open_positions=positions,
                               min_viable_notional=min_notional)


def test_precheck_allows_a_normal_cycle():
    assert precheck().can_act


def test_precheck_never_skips_when_a_position_is_open():
    """Delaying an exit to save money is exactly the wrong trade-off."""
    for state in [
        account(trades_today=99),
        account(settled_cash=0.0),
        account(equity=50_000.0, peak_equity=100_000.0),
        account(realized_pnl_today=-9_000.0),
    ]:
        assert precheck(a=state, positions=True).can_act
    assert precheck(m=market(kill_switch=True), positions=True).can_act
    assert precheck(m=market(minutes_until_close=1.0), positions=True).can_act


def test_precheck_skips_flat_account_at_daily_cap():
    r = precheck(a=account(trades_today=8))
    assert not r.can_act and "daily trade cap" in r.reason


def test_precheck_skips_flat_account_without_settled_cash():
    r = precheck(a=account(settled_cash=0.5), min_notional=100.0)
    assert not r.can_act and "settled cash" in r.reason


def test_precheck_skips_inside_blackout_windows():
    assert not precheck(m=market(minutes_since_open=3.0)).can_act
    assert not precheck(m=market(minutes_until_close=5.0)).can_act


def test_precheck_skips_when_kill_switch_engaged_and_flat():
    r = precheck(m=market(kill_switch=True))
    assert not r.can_act and "kill switch" in r.reason


def test_precheck_skips_after_daily_loss_limit():
    r = precheck(a=account(realized_pnl_today=-3_500.0))
    assert not r.can_act and "daily loss" in r.reason


def test_precheck_skips_after_drawdown_breach():
    r = precheck(a=account(equity=80_000.0, peak_equity=100_000.0))
    assert not r.can_act and "drawdown" in r.reason


def test_precheck_skips_closed_market():
    assert not precheck(m=market(is_open=False)).can_act


def test_precheck_agrees_with_the_gate():
    """If the pre-check says stop, a clean entry must indeed be rejected.

    A pre-check that skipped cycles the gate would have allowed would silently
    suppress real trades.
    """
    for state, mkt in [
        (account(trades_today=8), market()),
        (account(realized_pnl_today=-3_500.0), market()),
        (account(equity=80_000.0, peak_equity=100_000.0), market()),
        (account(), market(minutes_since_open=3.0)),
        (account(), market(kill_switch=True)),
    ]:
        assert not can_anything_happen(
            state, mkt, LIMITS, has_open_positions=False, min_viable_notional=1.0
        ).can_act
        assert not evaluate(proposal(), state, mkt, LIMITS).allowed
