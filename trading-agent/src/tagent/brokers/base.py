"""Broker interface.

Deliberately narrow. The agent reasons in terms of positions and orders; every
broker-specific concern (OAuth refresh, MCP transport, option symbology) stays
behind this boundary.

This matters more than usual here: Robinhood's agentic MCP is three months old,
its OAuth has a known token-persistence bug in some clients, and options support
is still rolling out. Any of that can change under us. A narrow interface means
switching to Alpaca is a config change, not a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


class BrokerError(RuntimeError):
    """Base for broker failures."""


class AuthExpired(BrokerError):
    """OAuth credentials need manual renewal.

    Raised separately because it is the one failure mode that requires a human
    and a browser. The loop must alert on it rather than retry into a wall.
    """


class OrderRejected(BrokerError):
    """The broker refused the order."""


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    last: float
    ts: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.bid and self.ask else self.last

    @property
    def spread_pct(self) -> float:
        m = self.mid
        return (self.ask - self.bid) / m if m > 0 else 1.0

    def age_seconds(self, now: datetime) -> float:
        return max(0.0, (now - self.ts).total_seconds())


@dataclass(frozen=True)
class Position:
    symbol: str
    asset_class: str
    quantity: float
    avg_price: float
    market_value: float
    unrealized_pnl: float
    max_loss: float = 0.0          # for defined-risk structures


@dataclass(frozen=True)
class Account:
    equity: float
    cash: float
    buying_power: float
    settled_cash: float
    positions: list[Position] = field(default_factory=list)

    @property
    def deployed_notional(self) -> float:
        return sum(abs(p.market_value) for p in self.positions)

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions)

    def exposure_by_symbol(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.positions:
            key = p.symbol.split()[0]  # option symbols carry the underlying first
            out[key] = out.get(key, 0.0) + max(abs(p.market_value), p.max_loss)
        return out


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    asset_class: str
    side: str
    quantity: float
    order_type: str = "limit"
    limit_price: float | None = None
    time_in_force: str = "day"
    client_tag: str | None = None      # our decision id, for reconciliation


@dataclass(frozen=True)
class OrderResult:
    broker_order_id: str
    status: str
    filled_quantity: float = 0.0
    filled_price: float | None = None


@runtime_checkable
class Broker(Protocol):
    name: str

    def account(self) -> Account: ...
    def quote(self, symbol: str) -> Quote: ...
    def place_order(self, req: OrderRequest) -> OrderResult: ...
    def cancel_order(self, broker_order_id: str) -> None: ...
    def open_orders(self) -> list[OrderResult]: ...
    def health_check(self) -> None:
        """Raise AuthExpired or BrokerError if the broker is not usable."""
        ...
