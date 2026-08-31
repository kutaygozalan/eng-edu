"""Reconciliation tests.

A bug here does not crash anything - it quietly writes wrong outcomes, and the
agent then learns confidently from corrupted statistics. That makes this the
most dangerous module in the system, so the tests are deliberately paranoid
about the four real-account cases: partial fills, partial closes, external
closes, and being run twice.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tagent.reconcile import (  # noqa: E402
    Lot, allocate_close, outcome_for, slippage_of,
)

T0 = datetime(2026, 9, 15, 14, 30, tzinfo=timezone.utc)


def lot(qty=10.0, price=100.0, direction=1, ts=None, max_loss=0.0, realized=0.0, lid=1,
        did=None):
    return Lot(
        id=lid, decision_id=did if did is not None else lid, symbol="AAPL",
        direction=direction, quantity_open=qty, quantity_total=qty,
        entry_price=price, entry_ts=(ts or T0).isoformat(),
        max_loss=max_loss or qty * price, realized_pnl=realized, fees=0.0,
    )


# ----------------------------------------------------------- basic matching --

def test_full_close_of_single_lot():
    allocs, unmatched = allocate_close([lot()], 10.0, exit_price=110.0)
    assert unmatched == 0
    assert len(allocs) == 1
    assert allocs[0].quantity == 10.0
    assert allocs[0].pnl == pytest.approx(100.0)     # 10 * $10
    assert allocs[0].fully_closed


def test_losing_trade_is_negative():
    allocs, _ = allocate_close([lot()], 10.0, exit_price=94.0)
    assert allocs[0].pnl == pytest.approx(-60.0)


def test_partial_close_leaves_lot_open():
    allocs, unmatched = allocate_close([lot(qty=10)], 4.0, exit_price=110.0)
    assert unmatched == 0
    assert allocs[0].quantity == 4.0
    assert allocs[0].pnl == pytest.approx(40.0)
    assert not allocs[0].fully_closed


def test_close_spans_multiple_lots_fifo():
    """Older lot must be consumed first - FIFO, not best-price."""
    old = lot(lid=1, qty=5, price=100, ts=T0)
    new = lot(lid=2, qty=5, price=120, ts=T0 + timedelta(days=1))
    allocs, unmatched = allocate_close([new, old], 8.0, exit_price=130.0)

    assert unmatched == 0
    assert [a.lot.id for a in allocs] == [1, 2]
    assert allocs[0].quantity == 5.0 and allocs[0].fully_closed
    assert allocs[1].quantity == 3.0 and not allocs[1].fully_closed
    assert allocs[0].pnl == pytest.approx(150.0)   # 5 * $30
    assert allocs[1].pnl == pytest.approx(30.0)    # 3 * $10


def test_closing_more_than_held_reports_unmatched():
    """Exposure we did not open must surface, not be absorbed silently."""
    allocs, unmatched = allocate_close([lot(qty=5)], 8.0, exit_price=110.0)
    assert sum(a.quantity for a in allocs) == 5.0
    assert unmatched == pytest.approx(3.0)


def test_close_with_no_lots_is_all_unmatched():
    allocs, unmatched = allocate_close([], 5.0, exit_price=100.0)
    assert allocs == [] and unmatched == 5.0


def test_zero_quantity_is_a_noop():
    assert allocate_close([lot()], 0.0, 100.0) == ([], 0.0)


def test_negative_quantity_is_a_noop():
    assert allocate_close([lot()], -5.0, 100.0) == ([], 0.0)


# ------------------------------------------------------------------ shorts --

def test_short_lot_profits_when_price_falls():
    allocs, _ = allocate_close([lot(direction=-1)], 10.0, exit_price=90.0)
    assert allocs[0].pnl == pytest.approx(100.0)


def test_short_lot_loses_when_price_rises():
    allocs, _ = allocate_close([lot(direction=-1)], 10.0, exit_price=110.0)
    assert allocs[0].pnl == pytest.approx(-100.0)


# -------------------------------------------------------------------- fees --

def test_fees_are_prorated_across_partial_closes():
    allocs, _ = allocate_close([lot(qty=10)], 4.0, exit_price=110.0, fees=2.0)
    # $2 of fees over 4 units closed = the whole $2 on this fill.
    assert allocs[0].pnl == pytest.approx(40.0 - 2.0)


def test_fees_split_across_multiple_lots():
    lots = [lot(lid=1, qty=5, ts=T0), lot(lid=2, qty=5, ts=T0 + timedelta(days=1))]
    allocs, _ = allocate_close(lots, 10.0, exit_price=110.0, fees=10.0)
    assert sum(a.pnl for a in allocs) == pytest.approx(100.0 - 10.0)


# --------------------------------------------------------------- outcomes ---

def test_outcome_uses_max_loss_as_denominator():
    """A defined-risk structure earns on what it RISKED, not its notional."""
    l = replace(lot(qty=1, price=10_000.0, max_loss=70.0), realized_pnl=21.0)
    out = outcome_for(l, (T0 + timedelta(days=3)).isoformat(), "target")
    assert out["pnl_pct"] == pytest.approx(0.30)      # 21/70, not 21/10000


def test_outcome_falls_back_to_notional_without_max_loss():
    l = replace(lot(qty=10, price=100.0, max_loss=0.0), realized_pnl=100.0)
    out = outcome_for(l, (T0 + timedelta(days=1)).isoformat(), "target")
    assert out["pnl_pct"] == pytest.approx(0.10)      # 100/1000


def test_outcome_holding_period():
    l = replace(lot(), realized_pnl=5.0)
    out = outcome_for(l, (T0 + timedelta(days=2, hours=12)).isoformat(), "target")
    assert out["holding_days"] == pytest.approx(2.5)


def test_intraday_holding_is_fractional_not_zero():
    l = replace(lot(), realized_pnl=5.0)
    out = outcome_for(l, (T0 + timedelta(hours=6)).isoformat(), "target")
    assert 0.2 < out["holding_days"] < 0.3


def test_outcome_carries_decision_id_of_the_opening_trade():
    """The whole point: P&L attributes to the decision that OPENED the position."""
    l = replace(lot(lid=99, did=42), realized_pnl=10.0)
    out = outcome_for(l, T0.isoformat(), "target")
    assert out["decision_id"] == 42


def test_zero_basis_does_not_divide_by_zero():
    l = replace(lot(qty=0.0, price=0.0, max_loss=0.0), realized_pnl=5.0)
    assert outcome_for(l, T0.isoformat(), "target")["pnl_pct"] == 0.0


# -------------------------------------------------------------- slippage ---

def test_buy_slippage_positive_when_paying_up():
    assert slippage_of("buy", 100.0, 100.05) == pytest.approx(0.05)


def test_buy_slippage_negative_on_price_improvement():
    assert slippage_of("buy", 100.0, 99.95) == pytest.approx(-0.05)


def test_sell_slippage_positive_when_filled_lower():
    assert slippage_of("sell", 100.0, 99.90) == pytest.approx(0.10)


def test_slippage_without_reference_price_is_zero():
    assert slippage_of("buy", None, 100.0) == 0.0
    assert slippage_of("buy", 0.0, 100.0) == 0.0


# --------------------------------------------- accumulation across closes ---

def test_partial_closes_compose_to_the_full_pnl():
    """Two exits from one lot must total what a single exit would have."""
    l = lot(qty=10, price=100.0)
    first, _ = allocate_close([l], 6.0, exit_price=110.0)
    remaining = replace(
        l, quantity_open=4.0, realized_pnl=l.realized_pnl + first[0].pnl
    )
    second, _ = allocate_close([remaining], 4.0, exit_price=115.0)

    assert first[0].pnl == pytest.approx(60.0)
    assert second[0].pnl == pytest.approx(60.0)
    assert second[0].fully_closed

    final = replace(remaining, realized_pnl=remaining.realized_pnl + second[0].pnl)
    out = outcome_for(final, T0.isoformat(), "target")
    assert out["pnl"] == pytest.approx(120.0)
    assert out["pnl_pct"] == pytest.approx(120.0 / 1000.0)


def test_float_dust_does_not_leave_a_lot_open():
    """Fractional-share quantities must still close cleanly."""
    l = lot(qty=0.1 + 0.2, price=100.0)      # 0.30000000000000004
    allocs, unmatched = allocate_close([l], 0.3, exit_price=110.0)
    assert allocs[0].fully_closed
    assert unmatched == 0
