"""A paper broker: the whole loop, none of the money.

Every end-to-end exercise of the trading path used to need a live Robinhood
account. That is a bad place to find out that reconciliation double-books a
partial fill, because the only way to get there was to place a real order.
This broker closes that gap - `cycle -> gate -> order -> reconcile -> lot ->
outcome -> review -> telemetry` runs start to finish, offline, for free.

WHAT THIS IS NOT
────────────────
It is **not a backtest**, and results from it say nothing about whether a
strategy makes money. Prices here are synthetic noise, not history: there is no
point-in-time data, no earnings, no corporate actions, no real liquidity, and
nothing to learn from. It exercises plumbing.

That distinction has teeth, because the agent writes lessons and setup
statistics from whatever it trades. Point `db_path` somewhere separate when
running on paper, or the agent carries beliefs learned from a random number
generator into the account that has your money in it. `tagent telemetry`
reports `broker_kind` so you can tell which database is which.

DESIGN
──────
*Prices are a pure function of (seed, symbol, timestamp)* - deterministic value
noise, no stored series. The same instant always yields the same price, so a
test is reproducible and a crashed cron run resumes on an identical market.

*Fills are pessimistic on purpose.* A paper broker that fills everything
instantly at the mid teaches the agent that spreads are free, which is the most
expensive lie a simulator can tell. So: everything crosses the spread, limit
orders fill only once the market actually reaches them, and large orders fill
in pieces. The one optimism left is queue priority, which is not modelled - see
`_fillable`.

*Cash settles on a delay* (T+1 by default), because `settled_cash` gates real
orders and a simulator where it always equals `cash` never exercises that path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .. import clock
from .base import (
    Account, BrokerError, OrderRejected, OrderRequest, OrderResult, Position,
    Quote,
)

BUY_SIDES = frozenset({"buy", "buy_to_open", "buy_to_close"})
SELL_SIDES = frozenset({"sell", "sell_to_open", "sell_to_close"})

# Origin for the price clock. Fixed, so a given wall-clock instant maps to the
# same point on the walk no matter when the state file was created.
_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Octaves of value noise: (period in minutes, weight). 390 is one trading
# session, so the coarse octave is a day-scale drift and the fine ones are the
# chop inside it.
_OCTAVES = ((390.0, 1.0), (90.0, 0.5), (20.0, 0.25))
_OCTAVE_NORM = sum(w for _, w in _OCTAVES)

DEFAULT_BASE_PRICE_RANGE = (20.0, 400.0)


def _unit(*parts: object) -> float:
    """A deterministic float in [0, 1) from the parts. Stable across runs."""
    digest = hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=8)
    return int.from_bytes(digest.digest(), "big") / 2.0**64


def _octave(seed: int, symbol: str, minutes: float, period: float) -> float:
    """Smoothly interpolated noise in [-1, 1] at one time scale."""
    scaled = minutes / period
    i = math.floor(scaled)
    frac = scaled - i
    a = _unit(seed, symbol, period, i) * 2.0 - 1.0
    b = _unit(seed, symbol, period, i + 1) * 2.0 - 1.0
    smooth = frac * frac * (3.0 - 2.0 * frac)   # C1-continuous, so no kinks
    return a + (b - a) * smooth


@dataclass
class _Order:
    id: str
    symbol: str
    asset_class: str
    side: str
    quantity: float
    order_type: str
    limit_price: float | None
    time_in_force: str
    client_tag: str | None
    created_ts: str
    status: str = "open"
    filled_quantity: float = 0.0      # CUMULATIVE; reconcile books the delta
    avg_price: float | None = None

    @property
    def remaining(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)

    def result(self) -> OrderResult:
        return OrderResult(
            broker_order_id=self.id, status=self.status,
            filled_quantity=self.filled_quantity, filled_price=self.avg_price,
        )


@dataclass
class _State:
    cash: float
    positions: dict[str, dict] = field(default_factory=dict)
    orders: list[dict] = field(default_factory=list)
    pending_settlements: list[dict] = field(default_factory=list)
    next_order_id: int = 1


class PaperBroker:
    """Broker protocol over a JSON file and a deterministic price function."""

    name = "paper"

    def __init__(
        self,
        state_file: str | Path,
        *,
        seed: int = 7,
        starting_cash: float = 2000.0,
        spread_pct: float = 0.0008,
        vol: float = 0.02,
        settle_days: int = 1,
        base_prices: dict[str, float] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.path = Path(state_file).expanduser()
        self.seed = int(seed)
        self.starting_cash = float(starting_cash)
        self.spread_pct = float(spread_pct)
        self.vol = float(vol)
        self.settle_days = int(settle_days)
        self.base_prices = {k.upper(): float(v) for k, v in (base_prices or {}).items()}
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._state = self._load()

    # ------------------------------------------------------------- state --
    def _load(self) -> _State:
        if not self.path.exists():
            return _State(cash=self.starting_cash)
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # Never silently reset to a fresh account: that would erase open
            # positions the ledger still believes in, and reconciliation would
            # book every one of them as an external close.
            raise BrokerError(f"paper state at {self.path} is unreadable: {exc}") from exc
        return _State(
            cash=float(raw.get("cash", self.starting_cash)),
            positions=dict(raw.get("positions") or {}),
            orders=list(raw.get("orders") or []),
            pending_settlements=list(raw.get("pending_settlements") or []),
            next_order_id=int(raw.get("next_order_id", 1)),
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        # Atomic: a cron job killed mid-write must not leave a half-file that
        # reads as a fresh account with no positions.
        tmp.write_text(json.dumps(asdict(self._state), indent=2, sort_keys=True))
        os.replace(tmp, self.path)

    def _orders(self) -> list[_Order]:
        return [_Order(**o) for o in self._state.orders]

    def _put_orders(self, orders: list[_Order]) -> None:
        self._state.orders = [asdict(o) for o in orders]

    # ------------------------------------------------------------ pricing --
    def base_price(self, symbol: str) -> float:
        symbol = symbol.upper()
        if symbol in self.base_prices:
            return self.base_prices[symbol]
        lo, hi = DEFAULT_BASE_PRICE_RANGE
        return lo + _unit(self.seed, "base", symbol) * (hi - lo)

    def price_at(self, symbol: str, when: datetime) -> float:
        """Mid price. Pure: same inputs, same answer, forever."""
        minutes = (when - _EPOCH).total_seconds() / 60.0
        combined = sum(
            w * _octave(self.seed, symbol.upper(), minutes, p) for p, w in _OCTAVES
        ) / _OCTAVE_NORM
        return self.base_price(symbol) * math.exp(self.vol * combined)

    def quote(self, symbol: str) -> Quote:
        now = self._now_fn()
        mid = self.price_at(symbol, now)
        half = mid * self.spread_pct / 2.0
        return Quote(
            symbol=symbol.upper(), bid=round(mid - half, 4),
            ask=round(mid + half, 4), last=round(mid, 4), ts=now,
        )

    # ------------------------------------------------------------- fills --
    def _fillable(self, order: _Order, q: Quote) -> float | None:
        """The price this order fills at right now, or None if it does not.

        Everything crosses the spread - buys lift the offer, sells hit the bid.
        Nobody gets the mid, which is the single most common way a simulator
        invents an edge that does not exist.

        The remaining optimism, stated plainly: queue priority is not modelled.
        A resting limit fills as soon as the market reaches it, where in a real
        book you might sit behind size and never trade. So limit strategies
        look somewhat better here than they would live.
        """
        if order.order_type == "market":
            return q.ask if order.side in BUY_SIDES else q.bid
        if order.limit_price is None:
            return None
        if order.side in BUY_SIDES:
            return q.ask if q.ask <= order.limit_price else None
        return q.bid if q.bid >= order.limit_price else None

    def _fill_quantity(self, order: _Order, now: datetime) -> float:
        """How much of the remainder clears this time.

        Deterministic per (order, minute), and often but not always the whole
        thing - partial fills are the case the ledger most needs to survive,
        and they never happen if the simulator is always generous.
        """
        remaining = order.remaining
        if remaining <= 1.0:
            return remaining
        share = 0.5 + 0.5 * _unit(self.seed, "fill", order.id, int(
            (now - _EPOCH).total_seconds() // 60))
        return min(remaining, math.ceil(remaining * share))

    def _evaluate(self, now: datetime) -> None:
        """Advance every open order to `now`: expire it, fill it, or leave it."""
        session = clock.session(now)
        today = now.astimezone(clock.ET).date()
        orders = self._orders()

        for order in orders:
            # "partially_filled" is still live: skipping it here would strand
            # the remainder forever, and the ledger would carry a half-filled
            # order that never resolves into an outcome.
            if order.status not in ("open", "partially_filled"):
                continue

            if order.time_in_force == "day" and _session_date(order) < today:
                order.status = "expired" if order.filled_quantity <= 0 else "filled"
                continue

            # Resting orders do not fill when the market is shut. Without this
            # an overnight cron run would fill at a price nobody could trade.
            if not session.is_open:
                continue

            q = self.quote(order.symbol)
            price = self._fillable(order, q)
            if price is None:
                continue

            qty = self._fill_quantity(order, now)
            if qty <= 0:
                continue
            try:
                self._apply_fill(order, qty, price, now)
            except OrderRejected:
                # Ran out of money or shares between placement and fill. The
                # order dies rather than overdrawing the account.
                order.status = "rejected"

        self._put_orders(orders)
        self._settle(today)

    def _apply_fill(self, order: _Order, qty: float, price: float, now: datetime) -> None:
        notional = qty * price
        symbol = order.symbol.upper()
        pos = self._state.positions.get(symbol, {"quantity": 0.0, "avg_price": 0.0})
        held = float(pos["quantity"])

        if order.side in BUY_SIDES:
            if notional > self._settled_cash(now) + 1e-9:
                raise OrderRejected(
                    f"{order.id}: needs ${notional:,.2f}, "
                    f"${self._settled_cash(now):,.2f} settled"
                )
            self._state.cash -= notional
            new_qty = held + qty
            if held >= 0:
                cost = held * float(pos["avg_price"]) + notional
                pos["avg_price"] = cost / new_qty if new_qty else 0.0
            pos["quantity"] = new_qty
        else:
            if order.side != "sell_to_open" and held - qty < -1e-9:
                raise OrderRejected(f"{order.id}: sells {qty:g} of {symbol}, holds {held:g}")
            self._state.cash += notional
            self._state.pending_settlements.append({
                "amount": notional,
                "settles_on": _settles_on(now, self.settle_days).isoformat(),
            })
            new_qty = held - qty
            if held <= 0:
                proceeds = abs(held) * float(pos["avg_price"]) + notional
                pos["avg_price"] = proceeds / abs(new_qty) if new_qty else 0.0
            pos["quantity"] = new_qty

        if abs(pos["quantity"]) < 1e-9:
            self._state.positions.pop(symbol, None)
        else:
            self._state.positions[symbol] = pos

        # Cumulative, with a running average price: this is what real brokers
        # report, and what `reconcile` subtracts its already-booked quantity
        # from to find the new slice.
        prior = order.filled_quantity * (order.avg_price or 0.0)
        order.filled_quantity += qty
        order.avg_price = (prior + notional) / order.filled_quantity
        order.status = "filled" if order.remaining <= 1e-9 else "partially_filled"

    def _settle(self, today: date) -> None:
        self._state.pending_settlements = [
            s for s in self._state.pending_settlements
            if date.fromisoformat(s["settles_on"]) > today
        ]

    def _settled_cash(self, now: datetime) -> float:
        today = now.astimezone(clock.ET).date()
        unsettled = sum(
            float(s["amount"]) for s in self._state.pending_settlements
            if date.fromisoformat(s["settles_on"]) > today
        )
        return max(0.0, self._state.cash - unsettled)

    # ---------------------------------------------------------- protocol --
    def positions(self) -> list[Position]:
        now = self._now_fn()
        out: list[Position] = []
        for symbol, pos in sorted(self._state.positions.items()):
            qty = float(pos["quantity"])
            if abs(qty) < 1e-9:
                continue
            avg = float(pos["avg_price"])
            mark = self.price_at(symbol, now)
            mv = qty * mark
            out.append(Position(
                symbol=symbol, asset_class="equity", quantity=qty, avg_price=avg,
                market_value=mv, unrealized_pnl=mv - qty * avg, max_loss=abs(mv),
            ))
        return out

    def account(self) -> Account:
        now = self._now_fn()
        self._evaluate(now)
        self._save()
        positions = self.positions()
        settled = self._settled_cash(now)
        return Account(
            equity=self._state.cash + sum(p.market_value for p in positions),
            cash=self._state.cash,
            # No margin, exactly like an agentic Robinhood account: what you can
            # spend is what has settled.
            buying_power=settled,
            settled_cash=settled,
            positions=positions,
        )

    def place_order(self, req: OrderRequest) -> OrderResult:
        now = self._now_fn()
        self._evaluate(now)

        if req.side not in BUY_SIDES | SELL_SIDES:
            raise OrderRejected(f"unknown side: {req.side}")
        if req.quantity <= 0:
            raise OrderRejected(f"quantity must be positive, got {req.quantity}")
        if req.order_type == "limit" and req.limit_price is None:
            raise OrderRejected("limit order without a limit price")
        if req.order_type not in ("limit", "market"):
            raise OrderRejected(f"unsupported order type: {req.order_type}")

        held = float(self._state.positions.get(req.symbol.upper(), {}).get("quantity", 0.0))
        if req.side in SELL_SIDES and req.side != "sell_to_open" and held - req.quantity < -1e-9:
            raise OrderRejected(
                f"cannot sell {req.quantity:g} {req.symbol}: holding {held:g}"
            )

        # A broker reserves buying power at the worst price you could pay, and
        # refuses up front rather than accepting an order it cannot honour.
        if req.side in BUY_SIDES:
            worst = req.limit_price if req.order_type == "limit" else self.quote(req.symbol).ask
            settled = self._settled_cash(now)
            if req.quantity * worst > settled + 1e-9:
                raise OrderRejected(
                    f"{req.symbol} {req.quantity:g} @ ${worst:,.2f} needs "
                    f"${req.quantity * worst:,.2f}, ${settled:,.2f} settled"
                )

        order = _Order(
            id=f"paper-{self._state.next_order_id:06d}",
            symbol=req.symbol.upper(), asset_class=req.asset_class, side=req.side,
            quantity=float(req.quantity), order_type=req.order_type,
            limit_price=req.limit_price, time_in_force=req.time_in_force,
            client_tag=req.client_tag, created_ts=now.isoformat(),
        )
        self._state.next_order_id += 1

        orders = self._orders()
        orders.append(order)
        self._put_orders(orders)

        # Give it one chance to fill now, so a marketable order behaves the way
        # anyone watching would expect rather than waiting for the next cycle.
        self._evaluate(now)
        self._save()

        current = next(o for o in self._orders() if o.id == order.id)
        return current.result()

    def cancel_order(self, broker_order_id: str) -> None:
        orders = self._orders()
        for order in orders:
            if order.id == broker_order_id:
                if order.status in ("open", "partially_filled"):
                    order.status = "cancelled"
                self._put_orders(orders)
                self._save()
                return
        raise BrokerError(f"no such order: {broker_order_id}")

    def all_orders(self) -> list[OrderResult]:
        now = self._now_fn()
        self._evaluate(now)
        self._save()
        return [o.result() for o in self._orders()]

    def open_orders(self) -> list[OrderResult]:
        # Evaluates first, like the other reads: an "open" order the market has
        # already filled is exactly the stale answer the loop would act on, by
        # declining to re-enter a position it in fact already holds.
        now = self._now_fn()
        self._evaluate(now)
        self._save()
        return [
            o.result() for o in self._orders()
            if o.status in ("open", "partially_filled")
        ]

    def health_check(self) -> None:
        try:
            self._save()
        except OSError as exc:
            raise BrokerError(f"paper state at {self.path} is not writable: {exc}") from exc


def _session_date(order: _Order) -> date:
    """The session a day order is working for.

    An order entered at 22:00 is not a stale order from a session that has
    already closed - it is queued for tomorrow. Expiring it on arrival would
    quietly discard everything placed by an after-hours cron run.
    """
    created = datetime.fromisoformat(order.created_ts).astimezone(clock.ET)
    d = created.date()
    if clock.session(created).is_open:
        return d
    if clock.is_trading_day(d) and created.time() < clock.REGULAR_OPEN:
        return d                              # placed pre-market, works today
    for offset in range(1, 11):
        nd = d + timedelta(days=offset)
        if clock.is_trading_day(nd):
            return nd
    return d


def _settles_on(now: datetime, days: int) -> date:
    """T+n in *trading* days: a Friday sale is not spendable on Saturday."""
    d = now.astimezone(clock.ET).date()
    remaining = max(0, days)
    while remaining > 0:
        d += timedelta(days=1)
        if clock.is_trading_day(d):
            remaining -= 1
    return d
