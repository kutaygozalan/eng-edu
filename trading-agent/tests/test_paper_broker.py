"""Tests for the paper broker.

Two jobs. The first is that it behaves like a broker the ledger can trust:
cumulative fill quantities, partial fills that finish, cash that settles on a
delay, state that survives the gap between two cron runs.

The second is that it is not *generous*. A simulator that fills everything at
the mid teaches the agent that spreads and queues are free, and the agent will
happily learn an edge that consists entirely of the simulator's optimism. Half
the tests below are asserting that something did NOT fill, or filled worse than
the market on offer.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tagent.brokers.base import (  # noqa: E402
    BrokerError, OrderRejected, OrderRequest,
)
from tagent.brokers.paper import PaperBroker  # noqa: E402
from tagent.memory.store import Store  # noqa: E402
from tagent.reconcile import reconcile  # noqa: E402

# Tuesday 2026-09-15, 10:00 ET. A normal open session: not a holiday, not a
# half day, well clear of both blackout windows.
OPEN = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)
CLOSED = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)      # 22:00 ET Monday
NEXT_DAY = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)


class Clock:
    """A movable `now`, so a test can span days without waiting for one."""

    def __init__(self, t=OPEN):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def paper(tmp_path):
    clock = Clock()
    broker = PaperBroker(
        tmp_path / "paper.json", starting_cash=2000.0,
        base_prices={"AAPL": 200.0}, now_fn=clock,
    )
    broker.test_clock = clock
    return broker


def buy(qty=1, **kw):
    kw.setdefault("order_type", "market")
    return OrderRequest(symbol="AAPL", asset_class="equity", side="buy",
                        quantity=qty, **kw)


def sell(qty=1, **kw):
    kw.setdefault("order_type", "market")
    return OrderRequest(symbol="AAPL", asset_class="equity", side="sell",
                        quantity=qty, **kw)


def marketable_buy(broker, qty):
    """A limit comfortably through the offer - marketable, but a real price."""
    return buy(qty, order_type="limit",
               limit_price=round(broker.quote("AAPL").ask * 1.02, 2))


def fill_completely(broker, req):
    """Place an order and let it finish; returns the final OrderResult."""
    placed = broker.place_order(req)
    for _ in range(10):
        broker.account()      # each call advances resting orders
        current = next(o for o in broker.all_orders()
                       if o.broker_order_id == placed.broker_order_id)
        if current.status not in ("open", "partially_filled"):
            return current
    raise AssertionError(f"order never settled: {placed}")


# ---------------------------------------------------------------- pricing ---

def test_price_is_a_pure_function_of_time(paper):
    assert paper.price_at("AAPL", OPEN) == paper.price_at("AAPL", OPEN)


def test_two_brokers_with_the_same_seed_see_the_same_market(tmp_path):
    a = PaperBroker(tmp_path / "a.json", seed=42, now_fn=Clock())
    b = PaperBroker(tmp_path / "b.json", seed=42, now_fn=Clock())
    assert a.price_at("AAPL", OPEN) == b.price_at("AAPL", OPEN)


def test_a_different_seed_is_a_different_market(tmp_path):
    a = PaperBroker(tmp_path / "a.json", seed=1, now_fn=Clock())
    b = PaperBroker(tmp_path / "b.json", seed=2, now_fn=Clock())
    assert a.price_at("AAPL", OPEN) != b.price_at("AAPL", OPEN)


def test_prices_move(paper):
    prices = {
        round(paper.price_at("AAPL", OPEN + timedelta(minutes=30 * i)), 6)
        for i in range(12)
    }
    assert len(prices) > 6, "a market that barely moves exercises nothing"


def test_prices_are_continuous(paper):
    """No teleporting: a minute apart must not be a different world."""
    a = paper.price_at("AAPL", OPEN)
    b = paper.price_at("AAPL", OPEN + timedelta(minutes=1))
    assert abs(b - a) / a < 0.01


def test_quote_is_bracketed_by_the_spread(paper):
    q = paper.quote("AAPL")
    assert q.bid < q.mid < q.ask
    assert q.spread_pct == pytest.approx(paper.spread_pct, rel=1e-3)


def test_quote_is_stamped_now_so_the_gate_sees_fresh_data(paper):
    assert paper.quote("AAPL").age_seconds(paper.test_clock.t) == 0.0


def test_base_price_can_be_pinned_per_symbol(paper):
    assert paper.price_at("AAPL", OPEN) == pytest.approx(200.0, rel=0.05)


# ------------------------------------------------------------------ fills ---

def test_market_buy_crosses_the_spread(paper):
    q = paper.quote("AAPL")
    result = paper.place_order(buy(1))
    assert result.filled_price == pytest.approx(q.ask)
    assert result.filled_price > q.mid, "filling at the mid is the simulator lying"


def test_market_sell_hits_the_bid(paper):
    fill_completely(paper, buy(1))
    q = paper.quote("AAPL")
    assert paper.place_order(sell(1)).filled_price == pytest.approx(q.bid)


def test_a_round_trip_loses_the_spread(paper):
    """The cost that makes most retail edges disappear must be present."""
    before = paper.account().equity
    fill_completely(paper, buy(1))
    fill_completely(paper, sell(1))
    assert paper.account().equity < before


def test_limit_order_away_from_the_market_does_not_fill(paper):
    result = paper.place_order(buy(1, order_type="limit", limit_price=100.0))
    assert result.status == "open"
    assert result.filled_quantity == 0.0


def test_limit_order_fills_once_the_market_reaches_it(paper):
    q = paper.quote("AAPL")
    result = paper.place_order(buy(1, order_type="limit", limit_price=q.ask + 5))
    assert result.status == "filled"


def test_a_marketable_limit_lifts_the_offer(paper):
    """You pay the ask, not the mid - and never worse than your own limit."""
    q = paper.quote("AAPL")
    limit = round(q.ask * 1.02, 2)
    result = paper.place_order(buy(1, order_type="limit", limit_price=limit))
    assert result.filled_price == pytest.approx(q.ask)
    assert result.filled_price <= limit
    assert result.filled_price > q.mid


def test_large_orders_fill_in_pieces(paper):
    """Partial fills are the case the ledger most needs to survive."""
    result = paper.place_order(marketable_buy(paper, 8))
    assert 0 < result.filled_quantity < 8
    assert result.status == "partially_filled"


def test_a_partial_fill_is_not_stranded(paper):
    order = fill_completely(paper, marketable_buy(paper, 8))
    assert order.filled_quantity == 8
    assert order.status == "filled"


def test_filled_quantity_is_cumulative(paper):
    """`reconcile` subtracts what it has booked from this number. If it ever
    reported a per-slice quantity instead, every partial fill would be
    double-booked."""
    placed = paper.place_order(marketable_buy(paper, 8))
    seen = [placed.filled_quantity]
    for _ in range(6):
        paper.account()
        seen.append(next(o for o in paper.all_orders()
                         if o.broker_order_id == placed.broker_order_id).filled_quantity)
    assert seen == sorted(seen)
    assert seen[-1] == 8


def test_nothing_fills_while_the_market_is_shut(paper):
    paper.test_clock.t = CLOSED
    result = paper.place_order(buy(1))
    assert result.filled_quantity == 0.0
    assert result.status == "open"


def test_an_order_placed_after_hours_fills_when_the_session_opens(paper):
    paper.test_clock.t = CLOSED
    placed = paper.place_order(buy(1))
    paper.test_clock.t = OPEN
    paper.account()
    current = next(o for o in paper.all_orders()
                   if o.broker_order_id == placed.broker_order_id)
    assert current.status == "filled"


def test_day_orders_expire_overnight(paper):
    placed = paper.place_order(buy(1, order_type="limit", limit_price=1.0))
    paper.test_clock.t = NEXT_DAY
    paper.account()
    current = next(o for o in paper.all_orders()
                   if o.broker_order_id == placed.broker_order_id)
    assert current.status == "expired"


# ------------------------------------------------------------------- cash ---

def test_buying_reduces_cash_by_the_notional(paper):
    before = paper.account().cash
    order = fill_completely(paper, marketable_buy(paper, 2))
    after = paper.account()
    assert after.cash == pytest.approx(before - order.filled_quantity * order.filled_price)


def test_sale_proceeds_do_not_settle_immediately(paper):
    fill_completely(paper, buy(1))
    fill_completely(paper, sell(1))
    acct = paper.account()
    assert acct.settled_cash < acct.cash, "unsettled proceeds must not be spendable"


def test_proceeds_settle_on_the_next_trading_day(paper):
    fill_completely(paper, buy(1))
    fill_completely(paper, sell(1))
    paper.test_clock.t = NEXT_DAY
    acct = paper.account()
    assert acct.settled_cash == pytest.approx(acct.cash)


def test_a_friday_sale_does_not_settle_on_saturday(paper):
    """T+1 counts trading days, not calendar days."""
    paper.test_clock.t = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)  # Friday
    fill_completely(paper, buy(1))
    fill_completely(paper, sell(1))
    paper.test_clock.t = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)  # Saturday
    acct = paper.account()
    assert acct.settled_cash < acct.cash


def test_buying_power_is_settled_cash_because_there_is_no_margin(paper):
    acct = paper.account()
    assert acct.buying_power == acct.settled_cash


def test_equity_is_cash_plus_what_the_positions_are_worth(paper):
    fill_completely(paper, marketable_buy(paper, 2))
    acct = paper.account()
    assert acct.equity == pytest.approx(
        acct.cash + sum(p.market_value for p in acct.positions)
    )


def test_an_order_larger_than_the_account_is_refused(paper):
    with pytest.raises(OrderRejected):
        paper.place_order(buy(1000))


def test_buying_power_is_reserved_at_the_limit_price(paper):
    """A broker holds the worst price you could pay, not the current one."""
    with pytest.raises(OrderRejected):
        paper.place_order(buy(1, order_type="limit", limit_price=1e9))


# -------------------------------------------------------------- rejections ---

def test_selling_what_you_do_not_hold_is_refused(paper):
    with pytest.raises(OrderRejected):
        paper.place_order(sell(5))


@pytest.mark.parametrize("bad", [
    buy(0), buy(-1),
    OrderRequest(symbol="AAPL", asset_class="equity", side="teleport", quantity=1),
    OrderRequest(symbol="AAPL", asset_class="equity", side="buy", quantity=1,
                 order_type="limit"),                       # no limit price
    OrderRequest(symbol="AAPL", asset_class="equity", side="buy", quantity=1,
                 order_type="iceberg"),
])
def test_malformed_orders_are_refused(paper, bad):
    with pytest.raises(OrderRejected):
        paper.place_order(bad)


# ------------------------------------------------------------------ state ---

def test_state_survives_between_runs(tmp_path):
    """The agent runs one-shot from cron: nothing survives in RAM, so an open
    position that does not survive in the file does not survive at all."""
    clock = Clock()
    first = PaperBroker(tmp_path / "s.json", starting_cash=2000.0,
                        base_prices={"AAPL": 200.0}, now_fn=clock)
    fill_completely(first, marketable_buy(first, 2))

    second = PaperBroker(tmp_path / "s.json", starting_cash=2000.0,
                         base_prices={"AAPL": 200.0}, now_fn=clock)
    held = {p.symbol: p.quantity for p in second.account().positions}
    assert held == {"AAPL": 2.0}
    assert second.account().cash == pytest.approx(first.account().cash)


def test_a_corrupt_state_file_raises_rather_than_resetting(tmp_path):
    """Silently starting fresh would drop open positions the ledger still
    believes in, and reconciliation would book every one as an external close."""
    path = tmp_path / "s.json"
    path.write_text("{ truncated")
    with pytest.raises(BrokerError):
        PaperBroker(path, now_fn=Clock())


def test_state_is_written_atomically(tmp_path):
    paper = PaperBroker(tmp_path / "s.json", now_fn=Clock())
    paper.account()
    assert json.loads((tmp_path / "s.json").read_text())["cash"] > 0
    assert not (tmp_path / "s.json.tmp").exists()


def test_open_orders_excludes_terminal_ones(paper):
    paper.place_order(buy(1, order_type="limit", limit_price=100.0))   # rests
    fill_completely(paper, buy(1))                                     # done
    assert len(paper.open_orders()) == 1
    assert len(paper.all_orders()) == 2


def test_cancelling_an_unknown_order_is_an_error(paper):
    with pytest.raises(BrokerError):
        paper.cancel_order("paper-999999")


def test_cancel_stops_a_resting_order(paper):
    placed = paper.place_order(buy(1, order_type="limit", limit_price=100.0))
    paper.cancel_order(placed.broker_order_id)
    assert paper.open_orders() == []


def test_health_check_passes_when_the_state_file_is_writable(paper):
    paper.health_check()


# ------------------------------------------------- the point of the exercise ---

def test_it_drives_the_full_ledger_loop(tmp_path):
    """Place, reconcile, hold, close, reconcile - and an outcome exists.

    This is why the paper broker is worth having: `outcomes` rows are what
    expectancy, calibration and every lesson in the journal are computed from,
    and until now the only way to produce one was a live account.
    """
    clock = Clock()
    broker = PaperBroker(tmp_path / "p.json", starting_cash=2000.0,
                         base_prices={"AAPL": 200.0}, now_fn=clock)
    store = Store(tmp_path / "t.db")

    decision_id = store.record_decision(
        cycle_id="c1", symbol="AAPL", asset_class="equity", side="buy",
        quantity=2, order_type="limit", notional=400.0, setup_tag="wheel",
        confidence=0.6, thesis="exercise the loop", gate_verdict="allow",
        gate_reasons=[], features={"max_loss": 400.0},
    )
    entry = fill_completely(broker, marketable_buy(broker, 2))
    store.mark_submitted(decision_id, entry.broker_order_id)

    reconcile(store, broker)
    assert store.open_lot_count() == 1, "the fill must become a lot"
    assert store.has_open_lots("AAPL")

    # Close it the next session, so the lot clears the external-close grace
    # period and the exit is attributed to the decision that OPENED it.
    clock.t = NEXT_DAY
    exit_id = store.record_decision(
        cycle_id="c2", symbol="AAPL", asset_class="equity", side="sell",
        quantity=2, order_type="market", notional=400.0, setup_tag="wheel",
        confidence=0.6, thesis="close", gate_verdict="allow", gate_reasons=[],
    )
    closed = fill_completely(broker, sell(2))
    store.mark_submitted(exit_id, closed.broker_order_id)

    reconcile(store, broker)

    assert store.open_lot_count() == 0, "the close must retire the lot"
    trades = store.closed_trades()
    assert len(trades) == 1
    assert trades[0]["setup_tag"] == "wheel"
    assert trades[0]["id"] == decision_id, "P&L attributes to the OPENING decision"
    assert trades[0]["pnl"] is not None
    store.close()
