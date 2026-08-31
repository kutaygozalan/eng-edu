"""Outcome reconciliation - the bridge between "placed an order" and "learned".

Without this module the agent records intentions and never finds out whether
they worked. Expectancy, calibration, and every lesson in the journal depend on
`outcomes` rows existing, and `outcomes` rows only exist because something
watched a position from fill to close and attributed the P&L back to the
decision that OPENED it.

That attribution is the whole difficulty. An exit is not its own trade; it is
the resolution of an earlier one. So opening fills create lots carrying the
opening decision_id, and closing fills consume those lots FIFO.

Four things this has to survive, because all four happen in real accounts:

  1. Partial fills - you ordered 5, you got 3.
  2. Partial closes - a lot closes over several exits, days apart.
  3. External closes - you sold it yourself in the app, or it was assigned.
     The position vanishes with no closing order of ours to match.
  4. Being run repeatedly - reconciliation runs every cycle and must never
     double-book a fill.

(4) is handled by `order_state.applied_quantity`: the broker reports cumulative
fill quantity, we record how much of it we have already booked, and only the
delta is applied.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .brokers.base import AuthExpired, Broker, BrokerError
from .memory.store import Store

CLOSING_SIDES = {"sell", "sell_to_close", "buy_to_close"}
OPENING_SIDES = {"buy", "buy_to_open", "sell_to_open"}
SHORT_OPENING = {"sell_to_open"}

TERMINAL_FAILED = {"cancelled", "canceled", "rejected", "failed", "expired"}

# A fill and the position it creates come from two different broker endpoints
# and do not update atomically. Without a grace period, a lot booked from a
# fresh fill can be seen as "missing from positions" microseconds later and
# closed as external - writing a fabricated outcome for a position that is very
# much still open. Real external closes are still caught on the next cycle.
EXTERNAL_CLOSE_GRACE_SECONDS = 300.0


@dataclass(frozen=True)
class Lot:
    id: int
    decision_id: int
    symbol: str
    direction: int          # +1 long, -1 short
    quantity_open: float
    quantity_total: float
    entry_price: float
    entry_ts: str
    max_loss: float
    realized_pnl: float
    fees: float


@dataclass(frozen=True)
class Allocation:
    """One lot's share of a closing fill."""

    lot: Lot
    quantity: float
    pnl: float
    fully_closed: bool


def allocate_close(
    lots: list[Lot], quantity: float, exit_price: float, fees: float = 0.0
) -> tuple[list[Allocation], float]:
    """Distribute a closing fill across open lots, FIFO.

    Returns (allocations, unmatched_quantity). Unmatched quantity is not an
    error to swallow: it means the account holds exposure we did not open (a
    manual buy, a transfer in), and the caller logs it rather than inventing a
    lot to absorb it.

    Fees are prorated by quantity so a partial close carries its share.
    """
    if quantity <= 0:
        return [], 0.0

    remaining = quantity
    fee_per_unit = fees / quantity if quantity else 0.0
    out: list[Allocation] = []

    for lot in sorted(lots, key=lambda l: l.entry_ts):
        if remaining <= 1e-9:
            break
        take = min(lot.quantity_open, remaining)
        if take <= 1e-9:
            continue
        # Direction makes shorts work without a separate branch: a short lot has
        # direction -1, so a fall in price is a gain.
        gross = (exit_price - lot.entry_price) * take * lot.direction
        pnl = gross - fee_per_unit * take
        out.append(
            Allocation(
                lot=lot,
                quantity=take,
                pnl=pnl,
                fully_closed=(lot.quantity_open - take) <= 1e-9,
            )
        )
        remaining -= take

    return out, max(0.0, remaining)


def outcome_for(lot: Lot, closed_ts: str, exit_reason: str) -> dict:
    """Build the outcomes row for a lot that has just fully closed.

    pnl_pct is return on CAPITAL AT RISK, using the max_loss the gate sized on.
    Using notional instead would flatter defined-risk structures enormously -
    a spread risking $70 on $10,000 notional would show a rounding error where
    it actually made 30% of what it put up.
    """
    basis = lot.max_loss if lot.max_loss > 0 else abs(
        lot.entry_price * lot.quantity_total
    )
    entry = datetime.fromisoformat(lot.entry_ts)
    exit_dt = datetime.fromisoformat(closed_ts)
    holding_days = max(0.0, (exit_dt - entry).total_seconds() / 86400.0)

    return {
        "decision_id": lot.decision_id,
        "pnl": lot.realized_pnl,
        "pnl_pct": lot.realized_pnl / basis if basis > 0 else 0.0,
        "holding_days": holding_days,
        "exit_reason": exit_reason,
        "fees": lot.fees,
    }


def slippage_of(side: str, intended_price: float | None, fill_price: float) -> float:
    """Signed cost of the fill versus the price we asked for.

    Positive means it cost us. Tracked because an agent whose edge estimates are
    fine but whose fills are consistently 15 bps worse than its limit has a real
    problem, and it is invisible in P&L alone.
    """
    if not intended_price or intended_price <= 0 or fill_price <= 0:
        return 0.0
    diff = fill_price - intended_price
    return diff if side in {"buy", "buy_to_open", "buy_to_close"} else -diff


# --------------------------------------------------------------- orchestration --

@dataclass
class ReconcileReport:
    checked: int = 0
    opened: int = 0
    closed: int = 0
    failed: int = 0
    external_closes: int = 0
    unmatched: list[str] = None
    errors: list[str] = None

    def __post_init__(self) -> None:
        self.unmatched = self.unmatched or []
        self.errors = self.errors or []

    def summary(self) -> str:
        return (
            f"reconcile: checked={self.checked} lots_opened={self.opened} "
            f"lots_closed={self.closed} orders_failed={self.failed} "
            f"external={self.external_closes} errors={len(self.errors)}"
        )


def reconcile(store: Store, broker: Broker) -> ReconcileReport:
    """Bring the ledger in line with the broker. Safe to run every cycle."""
    report = ReconcileReport()
    now = datetime.now(timezone.utc).isoformat()

    try:
        broker_orders = {o.broker_order_id: o for o in broker.all_orders()}
    except AuthExpired:
        raise
    except BrokerError as exc:
        store.log("error", "reconcile_orders_failed", str(exc))
        report.errors.append(f"could not list orders: {exc}")
        return report

    for pending in store.pending_orders():
        report.checked += 1
        oid = pending["broker_order_id"]
        order = broker_orders.get(oid)
        if order is None:
            # The broker no longer lists it. We cannot tell filled from purged,
            # so we leave the decision alone rather than guess at an outcome.
            store.log("warn", "order_not_found", f"{oid} absent from broker order list")
            continue

        status = (order.status or "").lower()
        already = float(pending["applied_quantity"] or 0.0)
        delta = float(order.filled_quantity or 0.0) - already

        if delta > 1e-9 and order.filled_price:
            try:
                _apply_fill(store, pending, order.filled_price, delta, now, report)
            except Exception as exc:  # noqa: BLE001 - one bad fill must not stop the rest
                store.log("error", "fill_apply_failed", f"{oid}: {exc}")
                report.errors.append(f"{oid}: {exc}")
                continue
            already += delta

        store.upsert_order_state(
            broker_order_id=oid,
            decision_id=int(pending["decision_id"]),
            status=status,
            filled_quantity=float(order.filled_quantity or 0.0),
            applied_quantity=already,
            filled_price=order.filled_price,
        )

        if status in TERMINAL_FAILED and already <= 1e-9:
            store.set_status(int(pending["decision_id"]), "cancelled")
            report.failed += 1
        elif already > 1e-9:
            store.set_status(int(pending["decision_id"]), "filled")

    _close_vanished(store, broker, now, report)
    store.log("info", "reconcile_complete", report.summary())
    return report


def _apply_fill(
    store: Store, pending: dict, price: float, quantity: float, now: str,
    report: ReconcileReport,
) -> None:
    side = pending["side"]
    decision_id = int(pending["decision_id"])

    if side in CLOSING_SIDES:
        # Always take this path, even with no open lots: allocate_close then
        # reports the whole quantity as unmatched, which is information we want.
        # Falling through to the opening branch would silently create a phantom
        # short lot out of what is really an accounting discrepancy.
        lots = store.open_lots(pending["symbol"])
        allocations, unmatched = allocate_close(lots, quantity, price)
        for alloc in allocations:
            store.apply_close(
                lot_id=alloc.lot.id,
                quantity=alloc.quantity,
                pnl=alloc.pnl,
                closed=alloc.fully_closed,
                closed_ts=now,
                exit_reason="agent_exit",
            )
            if alloc.fully_closed:
                lot = replace(
                    alloc.lot, realized_pnl=alloc.lot.realized_pnl + alloc.pnl
                )
                store.record_outcome(
                    slippage=slippage_of(side, pending["limit_price"], price),
                    **outcome_for(lot, now, "agent_exit"),
                )
                report.closed += 1
        if unmatched > 1e-9:
            msg = f"{pending['symbol']}: closed {unmatched:g} units with no open lot"
            store.log("warn", "unmatched_close", msg)
            report.unmatched.append(msg)
        return

    if side in OPENING_SIDES:
        store.open_lot(
            decision_id=decision_id,
            symbol=pending["symbol"],
            asset_class=pending["asset_class"],
            direction=-1 if side in SHORT_OPENING else 1,
            quantity=quantity,
            entry_price=price,
            entry_ts=now,
            max_loss=float(pending["max_loss"] or 0.0),
        )
        report.opened += 1


def _entry_epoch(lot: Lot) -> float:
    try:
        return datetime.fromisoformat(lot.entry_ts).timestamp()
    except ValueError:
        # An unparseable timestamp must not make a lot instantly eligible for
        # external close; treat it as brand new.
        return datetime.now(timezone.utc).timestamp()


def _close_vanished(
    store: Store, broker: Broker, now: str, report: ReconcileReport
) -> None:
    """Close lots whose position is no longer at the broker.

    You sold it in the app, it was assigned, a corporate action removed it. If
    these lots are never closed they sit open forever, the decisions that opened
    them never get outcomes, and those trades quietly vanish from the statistics
    the agent learns from - biasing them toward whatever the agent did close.
    """
    try:
        held = {p.symbol.split()[0]: abs(p.quantity) for p in broker.account().positions}
    except AuthExpired:
        raise
    except BrokerError as exc:
        store.log("warn", "reconcile_positions_failed", str(exc))
        return

    cutoff = datetime.now(timezone.utc).timestamp() - EXTERNAL_CLOSE_GRACE_SECONDS
    for symbol in store.symbols_with_open_lots():
        all_lots = store.open_lots(symbol)
        settled = [l for l in all_lots if _entry_epoch(l) <= cutoff]
        if not settled:
            continue

        # Compare the broker's holding against ALL our open lots, but only allow
        # SETTLED ones to absorb the difference - otherwise a just-filled lot
        # both inflates our count and becomes the thing we close.
        our_qty = sum(l.quantity_open for l in all_lots)
        excess = our_qty - held.get(symbol, 0.0)
        excess = min(excess, sum(l.quantity_open for l in settled))
        if excess <= 1e-6:
            continue
        lots = settled

        try:
            exit_price = broker.quote(symbol).mid
        except BrokerError:
            # Without a price we cannot value the close. Leave the lot open and
            # try again next cycle rather than book an invented number.
            store.log("warn", "external_close_no_quote", symbol)
            continue

        allocations, _ = allocate_close(lots, excess, exit_price)
        for alloc in allocations:
            store.apply_close(
                lot_id=alloc.lot.id, quantity=alloc.quantity, pnl=alloc.pnl,
                closed=alloc.fully_closed, closed_ts=now, exit_reason="external",
            )
            if alloc.fully_closed:
                lot = replace(
                    alloc.lot, realized_pnl=alloc.lot.realized_pnl + alloc.pnl
                )
                store.record_outcome(slippage=0.0, **outcome_for(lot, now, "external"))
                report.closed += 1
                report.external_closes += 1
        store.log(
            "warn", "external_close",
            f"{symbol}: {excess:g} units closed outside the agent",
        )
