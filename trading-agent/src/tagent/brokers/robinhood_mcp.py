"""Robinhood Agentic Trading broker, over their MCP server.

Robinhood does not publish the tool surface, and it has been moving: equities at
the May 2026 beta, crypto in July, options rolling out gradually. Hardcoding
guessed tool names would produce an agent that fails at 09:45 on a Monday with
"unknown tool".

So this adapter RESOLVES capabilities at runtime. It calls tools/list, scores
each tool against the capability it needs, and either binds it or fails at
startup with a list of what the server actually offers. `tagent discover` prints
that list so exact names can be pinned in config once, after which resolution is
deterministic.

Fail-at-startup is the point: a broker that cannot place an order should say so
during the 08:00 health check, not halfway through a trading decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from ..mcp.client import MCPClient, MCPError, ToolSpec
from .base import (
    Account, AuthExpired, Broker, BrokerError, OrderRejected, OrderRequest,
    OrderResult, Position, Quote,
)


@dataclass(frozen=True)
class Capability:
    """A thing the broker must be able to do, and how to recognise it."""

    key: str
    required: bool
    # Ordered: earlier patterns win. Matched against the tool name only -
    # descriptions are too noisy to score reliably.
    patterns: tuple[str, ...]
    negative: tuple[str, ...] = ()


CAPABILITIES: tuple[Capability, ...] = (
    Capability("get_account", True,
               (r"get_?accounts?$", r"accounts?_?info", r"account_?details", r"\baccounts?\b"),
               negative=(r"credit", r"card")),
    Capability("get_positions", True,
               (r"get_?positions?$", r"list_?positions?", r"\bpositions?\b", r"holdings"),
               negative=(r"option",)),
    Capability("get_quote", True,
               (r"get_?quotes?$", r"quotes?$", r"market_?data", r"price", r"last_?trade"),
               negative=(r"option", r"crypto", r"historical")),
    Capability("place_order", True,
               (r"place_?(equity_?)?order$", r"submit_?order", r"create_?order", r"buy$", r"order$"),
               negative=(r"option", r"crypto", r"review", r"cancel", r"get", r"list")),
    Capability("cancel_order", True,
               (r"cancel_?order", r"cancel$"), negative=(r"option", r"crypto")),
    Capability("list_orders", True,
               (r"get_?orders?$", r"list_?orders?$", r"orders?$", r"order_?history"),
               negative=(r"option", r"crypto", r"place", r"cancel")),
    # Options are optional: the rollout is incomplete, and at small account
    # sizes the risk gate will refuse them anyway.
    Capability("get_option_chain", False,
               (r"option.*chain", r"get_?options?$", r"options?_?chain")),
    Capability("place_option_order", False,
               (r"place_?option_?order", r"option_?order$"), negative=(r"review", r"cancel")),
)


def score_tool(name: str, cap: Capability) -> int:
    """Higher is better; 0 means no match. Earlier patterns score higher."""
    lname = name.lower()
    for pat in cap.negative:
        if re.search(pat, lname):
            return 0
    for i, pat in enumerate(cap.patterns):
        if re.search(pat, lname):
            return len(cap.patterns) - i
    return 0


def resolve_tools(
    tools: Iterable[ToolSpec], overrides: dict[str, str] | None = None
) -> tuple[dict[str, str], list[str]]:
    """Map capability key -> tool name. Returns (bindings, missing_required)."""
    overrides = overrides or {}
    by_name = {t.name for t in tools}
    bindings: dict[str, str] = {}
    missing: list[str] = []

    for cap in CAPABILITIES:
        if cap.key in overrides:
            pinned = overrides[cap.key]
            if pinned not in by_name:
                raise BrokerError(
                    f"config pins {cap.key}='{pinned}' but the server does not "
                    f"expose it. Run `tagent discover` for the current list."
                )
            bindings[cap.key] = pinned
            continue

        best, best_score = None, 0
        for t in tools:
            s = score_tool(t.name, cap)
            if s > best_score:
                best, best_score = t.name, s
        if best:
            bindings[cap.key] = best
        elif cap.required:
            missing.append(cap.key)

    return bindings, missing


def _num(d: dict, *keys: str, default: float = 0.0) -> float:
    """Brokers disagree about field naming; try several and coerce."""
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                continue
    return default


def _rows(payload: Any, *keys: str) -> list[dict]:
    """Unwrap a list that may arrive bare or nested under a key."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        if any(k in payload for k in ("symbol", "ticker")):
            return [payload]
    return []


class RobinhoodMCPBroker(Broker):
    name = "robinhood_mcp"

    def __init__(self, client: MCPClient, tool_overrides: dict[str, str] | None = None):
        self._c = client
        self._overrides = tool_overrides or {}
        self._tools: dict[str, str] | None = None

    # ----------------------------------------------------------- discovery --
    def bind(self) -> dict[str, str]:
        if self._tools is None:
            available = self._c.list_tools()
            bindings, missing = resolve_tools(available, self._overrides)
            if missing:
                names = ", ".join(sorted(t.name for t in available)) or "(none)"
                raise BrokerError(
                    "Robinhood MCP server does not expose required "
                    f"capabilities: {missing}.\nTools offered: {names}\n"
                    "Pin exact names under broker.tool_overrides in config."
                )
            self._tools = bindings
        return self._tools

    def _call(self, capability: str, args: dict | None = None) -> Any:
        tools = self.bind()
        tool = tools.get(capability)
        if not tool:
            raise BrokerError(f"capability '{capability}' is not available")
        return self._c.call_tool(tool, args or {})

    def supports_options(self) -> bool:
        return "place_option_order" in self.bind()

    # ------------------------------------------------------------- reading --
    def account(self) -> Account:
        raw = self._call("get_account")
        acct = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(acct, dict):
            raise BrokerError(f"unexpected account payload: {type(raw).__name__}")

        equity = _num(acct, "equity", "total_equity", "portfolio_value", "market_value")
        cash = _num(acct, "cash", "buying_power", "cash_available_for_withdrawal")
        buying_power = _num(acct, "buying_power", "cash", default=cash)

        # Agentic accounts have no margin borrowing, so unsettled proceeds are a
        # real constraint. When the server does not report settled cash we
        # assume NONE is settled rather than all of it - the conservative error
        # here rejects a trade; the optimistic one places an unfunded order.
        settled = _num(acct, "settled_cash", "cash_available_for_trading",
                       "unsettled_funds_excluded", default=-1.0)
        if settled < 0:
            unsettled = _num(acct, "unsettled_funds", default=0.0)
            settled = max(0.0, cash - unsettled) if unsettled else cash

        return Account(
            equity=equity or cash,
            cash=cash,
            buying_power=buying_power,
            settled_cash=settled,
            positions=self.positions(),
        )

    def positions(self) -> list[Position]:
        rows = _rows(self._call("get_positions"), "positions", "results", "data")
        out: list[Position] = []
        for p in rows:
            qty = _num(p, "quantity", "qty", "shares")
            if qty == 0:
                continue
            avg = _num(p, "average_buy_price", "avg_price", "average_price", "cost_basis")
            mv = _num(p, "market_value", "value", "equity", default=qty * avg)
            out.append(
                Position(
                    symbol=str(p.get("symbol") or p.get("ticker") or "?").upper(),
                    asset_class="option" if p.get("option_type") or p.get("strike_price")
                                else "equity",
                    quantity=qty,
                    avg_price=avg,
                    market_value=mv,
                    unrealized_pnl=_num(p, "unrealized_pnl", "total_return_today",
                                        default=mv - qty * avg),
                    max_loss=_num(p, "max_loss", default=abs(mv)),
                )
            )
        return out

    def quote(self, symbol: str) -> Quote:
        raw = self._call("get_quote", {"symbol": symbol})
        q = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(q, dict):
            raise BrokerError(f"unexpected quote payload for {symbol}")

        bid = _num(q, "bid_price", "bid")
        ask = _num(q, "ask_price", "ask")
        last = _num(q, "last_trade_price", "last", "price", "mark_price")
        if not last and bid and ask:
            last = (bid + ask) / 2
        if not (last or (bid and ask)):
            raise BrokerError(f"quote for {symbol} carried no usable price: {q}")

        ts = _parse_ts(q.get("updated_at") or q.get("timestamp") or q.get("time"))
        return Quote(symbol=symbol.upper(), bid=bid, ask=ask, last=last, ts=ts)

    def open_orders(self) -> list[OrderResult]:
        rows = _rows(self._call("list_orders"), "orders", "results", "data")
        return [
            OrderResult(
                broker_order_id=str(o.get("id") or o.get("order_id") or ""),
                status=str(o.get("state") or o.get("status") or "unknown"),
                filled_quantity=_num(o, "filled_quantity", "cumulative_quantity"),
                filled_price=_num(o, "average_price", "filled_price") or None,
            )
            for o in rows
            if str(o.get("state") or o.get("status") or "").lower()
            not in {"filled", "cancelled", "canceled", "rejected", "failed"}
        ]

    # ------------------------------------------------------------- writing --
    def place_order(self, req: OrderRequest) -> OrderResult:
        if req.asset_class == "option":
            if not self.supports_options():
                raise OrderRejected(
                    "this Robinhood agentic account does not expose an options "
                    "order tool yet (the rollout is staged). Trade equities or "
                    "switch broker.kind to alpaca."
                )
            capability = "place_option_order"
        else:
            capability = "place_order"

        args: dict[str, Any] = {
            "symbol": req.symbol,
            "side": req.side,
            "quantity": req.quantity,
            "type": req.order_type,
            "time_in_force": req.time_in_force,
        }
        if req.limit_price is not None:
            args["limit_price"] = round(req.limit_price, 2)
        if req.client_tag:
            args["client_order_id"] = req.client_tag

        try:
            raw = self._call(capability, args)
        except AuthExpired:
            raise
        except MCPError as exc:
            raise OrderRejected(f"broker refused order for {req.symbol}: {exc}") from exc

        o = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(o, dict):
            raise OrderRejected(f"unexpected order response: {raw!r}")

        oid = o.get("id") or o.get("order_id") or o.get("client_order_id")
        if not oid:
            # Without an id we cannot reconcile the fill, which means we cannot
            # attribute an outcome to the decision that caused it. Loud failure.
            raise OrderRejected(f"order response carried no id, cannot track: {o}")

        return OrderResult(
            broker_order_id=str(oid),
            status=str(o.get("state") or o.get("status") or "submitted"),
            filled_quantity=_num(o, "filled_quantity", "cumulative_quantity"),
            filled_price=_num(o, "average_price", "filled_price") or None,
        )

    def cancel_order(self, broker_order_id: str) -> None:
        self._call("cancel_order", {"order_id": broker_order_id})

    # -------------------------------------------------------------- health --
    def health_check(self) -> None:
        """Prove we can authenticate, resolve tools, and read the account."""
        self.bind()
        acct = self.account()
        if acct.equity <= 0 and acct.cash <= 0:
            raise BrokerError(
                "connected, but the agentic account reports zero equity and "
                "zero cash - is it funded?"
            )


def _parse_ts(value: Any) -> datetime:
    """Quote timestamps drive the staleness check, so a missing one is NOT 'now'.

    Claiming freshness we cannot prove would defeat the gate's stale-data rule.
    An unparseable timestamp yields epoch, which reads as maximally stale and
    causes a rejection - the safe direction.
    """
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc)
