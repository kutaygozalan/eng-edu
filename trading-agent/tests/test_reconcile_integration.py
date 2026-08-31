"""End-to-end reconciliation against a fake broker.

Covers the orchestration that the pure-function tests cannot: idempotency across
repeated runs, partial fills completing later, and positions that disappear
without one of our orders closing them.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tagent.brokers.base import Account, OrderResult, Position, Quote  # noqa: E402
from tagent.memory.store import Store  # noqa: E402
from tagent.reconcile import reconcile  # noqa: E402


def age_lots(store, seconds=600):
    """Backdate open lots so they clear the external-close grace period."""
    from datetime import timedelta
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    store._conn.execute("UPDATE lots SET entry_ts=? WHERE status='open'", (old,))


class FakeBroker:
    name = "fake"

    def __init__(self):
        self.orders: dict[str, OrderResult] = {}
        self.positions: list[Position] = []
        self.prices: dict[str, float] = {"AAPL": 100.0}

    def all_orders(self):
        return list(self.orders.values())

    def open_orders(self):
        return [o for o in self.orders.values() if o.status not in
                {"filled", "cancelled", "rejected"}]

    def account(self):
        return Account(equity=2000.0, cash=1000.0, buying_power=1000.0,
                       settled_cash=1000.0, positions=list(self.positions))

    def quote(self, symbol):
        px = self.prices[symbol]
        return Quote(symbol=symbol, bid=px - 0.01, ask=px + 0.01, last=px,
                     ts=datetime.now(timezone.utc))

    def place_order(self, req): raise NotImplementedError
    def cancel_order(self, oid): raise NotImplementedError
    def health_check(self): pass

    # test helpers -------------------------------------------------------
    def fill(self, oid, qty, price, status="filled"):
        self.orders[oid] = OrderResult(oid, status, qty, price)

    def hold(self, symbol, qty, avg):
        self.positions = [p for p in self.positions if p.symbol != symbol]
        if qty:
            self.positions.append(
                Position(symbol=symbol, asset_class="equity", quantity=qty,
                         avg_price=avg, market_value=qty * self.prices[symbol],
                         unrealized_pnl=0.0)
            )


@pytest.fixture
def store():
    s = Store(Path(tempfile.mkdtemp()) / "t.db")
    yield s
    s.close()


def submit(store, broker, oid, side, qty, max_loss=1000.0):
    did = store.record_decision(
        cycle_id="c1", symbol="AAPL", asset_class="equity", side=side,
        quantity=qty, order_type="limit", notional=qty * 100, setup_tag="gap_fade",
        confidence=0.6, thesis="test", limit_price=100.0,
        features={"max_loss": max_loss}, gate_verdict="allow", gate_reasons=[],
        status="proposed",
    )
    store.mark_submitted(did, oid)
    return did


# ------------------------------------------------------------ happy path ---

def test_open_then_close_writes_one_outcome(store):
    b = FakeBroker()
    buy = submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0)
    b.hold("AAPL", 10, 100.0)

    r = reconcile(store, b)
    assert r.opened == 1 and r.closed == 0
    assert store.open_lot_count() == 1
    assert store.closed_trades() == []

    sell = submit(store, b, "o2", "sell", 10)
    b.fill("o2", 10, 112.0)
    b.hold("AAPL", 0, 0)

    r = reconcile(store, b)
    assert r.closed == 1
    trades = store.closed_trades()
    assert len(trades) == 1
    # P&L attributes to the OPENING decision, not the sell.
    assert trades[0]["id"] == buy
    assert trades[0]["pnl"] == pytest.approx(120.0)
    assert trades[0]["pnl_pct"] == pytest.approx(0.12)   # on $1000 max_loss
    assert trades[0]["was_win"] == 1


def test_losing_trade_records_as_loss(store):
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0); b.hold("AAPL", 10, 100.0)
    reconcile(store, b)

    submit(store, b, "o2", "sell", 10)
    b.fill("o2", 10, 92.0); b.hold("AAPL", 0, 0)
    reconcile(store, b)

    t = store.closed_trades()[0]
    assert t["pnl"] == pytest.approx(-80.0) and t["was_win"] == 0


# ----------------------------------------------------------- idempotency ---

def test_running_twice_does_not_double_book(store):
    """Reconciliation runs every cycle. Booking a fill twice would double P&L."""
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0); b.hold("AAPL", 10, 100.0)

    for _ in range(3):
        reconcile(store, b)
    assert store.open_lot_count() == 1
    assert sum(l.quantity_open for l in store.open_lots("AAPL")) == 10.0


def test_repeated_reconcile_after_close_keeps_one_outcome(store):
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0); b.hold("AAPL", 10, 100.0)
    reconcile(store, b)
    submit(store, b, "o2", "sell", 10)
    b.fill("o2", 10, 110.0); b.hold("AAPL", 0, 0)

    for _ in range(3):
        reconcile(store, b)
    trades = store.closed_trades()
    assert len(trades) == 1
    assert trades[0]["pnl"] == pytest.approx(100.0)


# --------------------------------------------------------- partial fills ---

def test_partial_fill_then_completion_books_the_delta_only(store):
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)

    b.fill("o1", 4, 100.0, status="partially_filled")
    b.hold("AAPL", 4, 100.0)
    reconcile(store, b)
    assert sum(l.quantity_open for l in store.open_lots("AAPL")) == 4.0

    b.fill("o1", 10, 100.0, status="filled")
    b.hold("AAPL", 10, 100.0)
    reconcile(store, b)
    # 4 booked, then the 6-unit delta - not 14.
    assert sum(l.quantity_open for l in store.open_lots("AAPL")) == 10.0


def test_partial_close_defers_the_outcome(store):
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0); b.hold("AAPL", 10, 100.0)
    reconcile(store, b)

    submit(store, b, "o2", "sell", 4)
    b.fill("o2", 4, 110.0); b.hold("AAPL", 6, 100.0)
    reconcile(store, b)
    assert store.closed_trades() == []            # not resolved yet
    assert store.open_lots("AAPL")[0].quantity_open == 6.0

    submit(store, b, "o3", "sell", 6)
    b.fill("o3", 6, 105.0); b.hold("AAPL", 0, 0)
    reconcile(store, b)

    trades = store.closed_trades()
    assert len(trades) == 1
    assert trades[0]["pnl"] == pytest.approx(40.0 + 30.0)  # both legs accumulated


# -------------------------------------------------------- external closes --

def test_position_sold_outside_the_agent_is_closed(store):
    """Sold in the app. The lot must not sit open forever, silently biasing stats."""
    b = FakeBroker()
    buy = submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0); b.hold("AAPL", 10, 100.0)
    reconcile(store, b)

    age_lots(store)
    b.hold("AAPL", 0, 0)          # vanished, with no order of ours
    b.prices["AAPL"] = 107.0
    r = reconcile(store, b)

    assert r.external_closes == 1
    trades = store.closed_trades()
    assert len(trades) == 1
    assert trades[0]["id"] == buy
    assert trades[0]["exit_reason"] == "external"
    assert trades[0]["pnl"] == pytest.approx(70.0)


def test_partial_external_close_only_closes_the_excess(store):
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0); b.hold("AAPL", 10, 100.0)
    reconcile(store, b)

    age_lots(store)
    b.hold("AAPL", 6, 100.0)      # 4 disappeared
    reconcile(store, b)
    assert store.open_lots("AAPL")[0].quantity_open == 6.0
    assert store.closed_trades() == []


def test_external_close_skipped_when_no_quote(store):
    """Better to leave the lot open than book an invented exit price."""
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0); b.hold("AAPL", 10, 100.0)
    reconcile(store, b)

    age_lots(store)
    b.hold("AAPL", 0, 0)
    del b.prices["AAPL"]

    class NoQuote(FakeBroker):
        def quote(self, symbol):
            from tagent.brokers.base import BrokerError
            raise BrokerError("no quote")
    nq = NoQuote()
    nq.orders, nq.positions = b.orders, b.positions

    r = reconcile(store, nq)
    assert r.external_closes == 0
    assert store.open_lot_count() == 1


# ------------------------------------------------------------ cancelled ----

def test_cancelled_order_creates_no_lot(store):
    b = FakeBroker()
    did = submit(store, b, "o1", "buy", 10)
    b.fill("o1", 0, 0.0, status="cancelled")

    r = reconcile(store, b)
    assert r.failed == 1 and r.opened == 0
    assert store.open_lot_count() == 0


def test_partially_filled_then_cancelled_keeps_the_filled_part(store):
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 3, 100.0, status="cancelled")
    b.hold("AAPL", 3, 100.0)

    r = reconcile(store, b)
    assert r.opened == 1
    assert store.open_lots("AAPL")[0].quantity_open == 3.0


def test_order_missing_from_broker_is_left_alone(store):
    """We cannot tell 'filled and purged' from 'never existed'. Do not guess."""
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    r = reconcile(store, b)
    assert r.opened == 0 and r.closed == 0 and r.failed == 0
    assert store.open_lot_count() == 0


def test_close_with_no_matching_lot_is_reported(store):
    b = FakeBroker()
    submit(store, b, "o1", "sell", 5)
    b.fill("o1", 5, 100.0)
    r = reconcile(store, b)
    assert r.unmatched and "no open lot" in r.unmatched[0]


# -------------------------------------------------- settlement race guard ---

def test_fresh_fill_is_not_mistaken_for_an_external_close(store):
    """Fills and positions come from different endpoints and are not atomic.

    Without a grace period, a lot booked from a fresh fill gets closed as
    "external" the moment the positions endpoint has not caught up - fabricating
    an outcome for a position that is still very much open, and poisoning the
    statistics the agent learns from.
    """
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0)
    # Deliberately do NOT call b.hold: the position has not propagated yet.

    r = reconcile(store, b)
    assert r.opened == 1
    assert r.external_closes == 0
    assert store.open_lot_count() == 1
    assert store.closed_trades() == []


def test_external_close_still_detected_after_the_grace_period(store):
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0)
    reconcile(store, b)

    age_lots(store)
    r = reconcile(store, b)
    assert r.external_closes == 1
    assert store.open_lot_count() == 0


def test_fresh_lot_does_not_absorb_an_older_lots_disappearance(store):
    """Only settled lots may be closed, even when the shortfall is real."""
    b = FakeBroker()
    submit(store, b, "o1", "buy", 10)
    b.fill("o1", 10, 100.0)
    b.hold("AAPL", 10, 100.0)
    reconcile(store, b)
    age_lots(store)                      # first lot is now settled

    submit(store, b, "o2", "buy", 5)
    b.fill("o2", 5, 100.0)
    b.hold("AAPL", 5, 100.0)             # the OLD 10 vanished; new 5 remains
    r = reconcile(store, b)

    assert r.opened == 1
    # The 10-unit settled lot closes; the fresh 5-unit lot survives.
    remaining = store.open_lots("AAPL")
    assert len(remaining) == 1
    assert remaining[0].quantity_open == 5.0
