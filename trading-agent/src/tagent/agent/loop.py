"""The trading cycle.

Runs every 20 minutes during market hours. One pass:

    read broker -> assemble bounded context -> ask the model for proposals
        -> RISK GATE (deterministic) -> place surviving orders -> record everything

Two properties worth stating explicitly, because they are what make the whole
design hold together:

  * The model NEVER touches the broker. It returns structured proposals; this
    module is the only thing that can place an order, and it can only do so
    after `gate.evaluate` returns ALLOW. Nothing the model can emit - no
    argument, no urgency, no claimed exception - routes around that.

  * EVERY proposal is recorded, including rejected ones. The rejection log is a
    primary input to the nightly review; an agent that keeps proposing 0DTE
    should learn that about itself, and it can only learn it if we wrote it down.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import anthropic

from .. import clock
from ..brokers.base import (
    Account, AuthExpired, Broker, BrokerError, OrderRejected, OrderRequest, Quote,
)
from ..config import Config
from ..memory.store import Store
from ..reconcile import reconcile
from ..risk import gate as G
from . import context as ctx
from .prompts import SYSTEM_PROMPT, PROPOSAL_SCHEMA

MAX_TOKENS = 16000


@dataclass
class CycleReport:
    cycle_id: str
    proposed: int = 0
    allowed: int = 0
    placed: int = 0
    rejected: int = 0
    errors: list[str] = None
    skipped_reason: str | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    def summary(self) -> str:
        if self.skipped_reason:
            return f"cycle {self.cycle_id}: skipped ({self.skipped_reason})"
        return (
            f"cycle {self.cycle_id}: proposed={self.proposed} "
            f"allowed={self.allowed} placed={self.placed} "
            f"rejected={self.rejected} errors={len(self.errors)}"
        )


def run_cycle(cfg: Config, store: Store, broker: Broker) -> CycleReport:
    cycle_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    report = CycleReport(cycle_id=cycle_id)

    session = clock.session(now)
    if not session.is_open:
        report.skipped_reason = f"market closed ({session.reason})"
        return report

    # The kill switch is checked here as well as in the gate. Here it saves the
    # cost of an LLM call; there it is the actual enforcement.
    if store.kill_switch:
        reason = store.get_state("kill_switch_reason", "unknown")
        store.log("warn", "cycle_skipped", f"kill switch engaged: {reason}")
        report.skipped_reason = f"kill switch engaged: {reason}"
        return report

    # Reconcile FIRST. Fills from previous cycles become lots and outcomes here,
    # so the account snapshot, the expectancy statistics and the churn check all
    # reflect what actually happened rather than what we last intended.
    try:
        recon = reconcile(store, broker)
        if recon.errors:
            report.errors.extend(recon.errors)
    except AuthExpired as exc:
        store.log("critical", "auth_expired", str(exc))
        report.errors.append(f"auth expired: {exc}")
        return report

    try:
        account = broker.account()
    except AuthExpired as exc:
        # The one failure that needs a human and a browser. Never retried.
        store.log("critical", "auth_expired", str(exc))
        report.errors.append(f"auth expired: {exc}")
        return report
    except BrokerError as exc:
        store.log("error", "broker_unreachable", str(exc))
        report.errors.append(f"broker error: {exc}")
        return report

    day_start = clock.session_day_start_utc(now)
    sod_equity = store.start_of_day_equity(day_start) or account.equity
    peak, drawdown = store.record_equity(
        account.equity, account.cash,
        account.deployed_notional / account.equity if account.equity else 0.0,
    )

    # Trip the kill switch from the equity curve rather than waiting for a
    # proposal to be gated: if we are past the drawdown limit we want the switch
    # latched now, so it survives a restart and blocks the next cycle too.
    if drawdown >= cfg.limits.max_drawdown_pct:
        store.engage_kill_switch(
            f"drawdown {drawdown:.2%} breached limit {cfg.limits.max_drawdown_pct:.2%}"
        )
        report.skipped_reason = f"drawdown limit breached ({drawdown:.2%})"
        return report

    quotes = _fetch_quotes(broker, cfg.agent.universe, store)
    open_orders = _safe(lambda: broker.open_orders(), [], store, "open_orders")
    todays = store.rejections(day_start)[-10:]

    account_state = _account_state(
        account, sod_equity, peak, store, day_start, cycle_id
    )

    # Skip the model entirely when no order could pass the gate anyway. On this
    # cadence the LLM call dominates running cost, and a flat account that has
    # spent its daily trade budget cannot do anything with the answer.
    precheck = G.can_anything_happen(
        account_state,
        G.MarketContext(
            now=now, is_open=session.is_open,
            minutes_since_open=session.minutes_since_open,
            minutes_until_close=session.minutes_until_close,
            kill_switch=store.kill_switch,
        ),
        cfg.limits,
        has_open_positions=bool(account.positions) or store.open_lot_count() > 0,
        min_viable_notional=_min_viable_notional(cfg, quotes),
    )
    if not precheck.can_act:
        store.log("info", "cycle_skipped_precheck", precheck.reason)
        report.skipped_reason = precheck.reason
        return report

    proposals = _ask_model(cfg, store, session, account, quotes, todays, open_orders, now)
    report.proposed = len(proposals)

    trades_this_cycle = 0
    for p in proposals:
        try:
            proposal, order = _to_proposal(p, quotes, account, now)
        except ValueError as exc:
            # A malformed proposal is a process error worth recording, not a
            # crash: the review should see that the model emitted garbage.
            store.log("warn", "malformed_proposal", str(exc), raw=p)
            report.errors.append(f"malformed proposal: {exc}")
            continue

        state = _with_cycle_count(account_state, trades_this_cycle)
        market = G.MarketContext(
            now=now,
            is_open=session.is_open,
            minutes_since_open=session.minutes_since_open,
            minutes_until_close=session.minutes_until_close,
            blocked_setups=store.blocked_setups(),
            universe=frozenset(cfg.agent.universe) if cfg.agent.universe else None,
            kill_switch=store.kill_switch,
        )
        result = G.evaluate(proposal, state, market, cfg.limits)

        decision_id = store.record_decision(
            cycle_id=cycle_id,
            symbol=proposal.symbol,
            asset_class=proposal.asset_class,
            side=proposal.side,
            quantity=proposal.quantity,
            order_type=order.order_type,
            notional=proposal.notional,
            setup_tag=proposal.setup_tag,
            regime_tag=p.get("regime_tag"),
            confidence=proposal.confidence,
            thesis=proposal.thesis,
            limit_price=order.limit_price,
            features={
                "expected_edge_pct": proposal.expected_edge_pct,
                "est_spread_pct": proposal.est_spread_pct,
                "max_loss": proposal.max_loss,
                "dte": proposal.dte,
            },
            gate_verdict=result.verdict.value,
            gate_reasons=[r.value for r in result.reasons],
            status="proposed" if result.allowed else "rejected",
        )

        if not result.allowed:
            report.rejected += 1
            store.log(
                "info", "gate_reject",
                f"{proposal.symbol} {proposal.side}: {'; '.join(result.details)}",
                decision_id=decision_id,
            )
            continue

        report.allowed += 1

        if cfg.dry_run:
            store.log(
                "info", "dry_run",
                f"would place {proposal.side} {proposal.quantity} {proposal.symbol}",
                decision_id=decision_id,
            )
            trades_this_cycle += 1
            continue

        try:
            placed = broker.place_order(order)
        except AuthExpired as exc:
            store.log("critical", "auth_expired", str(exc), decision_id=decision_id)
            store.set_status(decision_id, "failed")
            report.errors.append(f"auth expired mid-cycle: {exc}")
            break
        except (OrderRejected, BrokerError) as exc:
            store.set_status(decision_id, "failed")
            store.log("error", "order_failed", str(exc), decision_id=decision_id)
            report.errors.append(f"{proposal.symbol}: {exc}")
            continue

        store.mark_submitted(decision_id, placed.broker_order_id)
        report.placed += 1
        trades_this_cycle += 1
        store.log(
            "info", "order_placed",
            f"{proposal.side} {proposal.quantity} {proposal.symbol} "
            f"-> {placed.broker_order_id} ({placed.status})",
            decision_id=decision_id,
        )

    store.log("info", "cycle_complete", report.summary())
    return report


# ------------------------------------------------------------------ model --

def _ask_model(
    cfg: Config,
    store: Store,
    session: clock.MarketSession,
    account: Account,
    quotes: list[Quote],
    todays: list[dict],
    open_orders: list,
    now: datetime,
) -> list[dict]:
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    stable = ctx.build_stable_block(
        lessons=store.active_lessons(cfg.agent.max_context_lessons),
        setup_stats=store.setup_stats(),
        calibration=store.calibration(),
        universe=cfg.agent.universe,
        setups=cfg.agent.setups,
        limits_summary={
            "max_position_pct": cfg.limits.max_position_pct,
            "max_trades_per_cycle": cfg.limits.max_trades_per_cycle,
            "min_dte": cfg.limits.min_dte,
            "require_defined_risk": cfg.limits.require_defined_risk,
            "min_edge_cost_multiple": cfg.limits.min_edge_cost_multiple,
        },
    )

    similar: list[dict] = []
    for setup in cfg.agent.setups[:3]:
        similar.extend(store.similar_decisions(setup, None, limit=2))

    volatile = ctx.build_volatile_block(
        now_iso=now.isoformat(),
        session={
            "minutes_since_open": session.minutes_since_open,
            "minutes_until_close": session.minutes_until_close,
            "is_early_close": session.is_early_close,
        },
        account={
            "equity": account.equity,
            "settled_cash": account.settled_cash,
            "buying_power": account.buying_power,
            "deployed_notional": account.deployed_notional,
            "unrealized_pnl": account.unrealized_pnl,
            "realized_pnl_today": 0.0,
            "trades_today": store.trades_today(clock.session_day_start_utc(now)),
        },
        positions=[
            {
                "symbol": p.symbol, "quantity": p.quantity, "avg_price": p.avg_price,
                "market_value": p.market_value, "unrealized_pnl": p.unrealized_pnl,
            }
            for p in account.positions
        ],
        quotes=[
            {
                "symbol": q.symbol, "bid": q.bid, "ask": q.ask, "last": q.last,
                "spread_pct": q.spread_pct, "age_seconds": q.age_seconds(now),
            }
            for q in quotes
        ],
        todays_decisions=todays,
        similar=similar,
        open_orders=[
            {"broker_order_id": o.broker_order_id, "status": o.status}
            for o in open_orders
        ],
    )

    try:
        response = client.messages.create(
            model=cfg.agent.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": PROPOSAL_SCHEMA},
            },
            system=[
                {"type": "text", "text": SYSTEM_PROMPT},
                # Accumulated knowledge changes nightly, so it sits behind the
                # cache breakpoint; only the per-cycle block below is uncached.
                {
                    "type": "text",
                    "text": stable,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": volatile}],
        )
    except anthropic.APIStatusError as exc:
        store.log("error", "model_error", f"{exc.status_code}: {exc.message}")
        return []
    except anthropic.APIConnectionError as exc:
        store.log("error", "model_unreachable", str(exc))
        return []

    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", "") or ""
        store.log("warn", "model_refusal", detail)
        return []

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        store.log("warn", "unparseable_model_output", str(exc), raw=text[:500])
        return []

    proposals = payload.get("proposals", [])
    if not isinstance(proposals, list):
        return []
    # The gate enforces the per-cycle cap too; trimming here just avoids
    # constructing orders we already know will be refused.
    return proposals[: cfg.limits.max_trades_per_cycle]


# ------------------------------------------------------------- conversion --

def _to_proposal(
    p: dict, quotes: list[Quote], account: Account, now: datetime
) -> tuple[G.Proposal, OrderRequest]:
    """Turn a model proposal into a gate Proposal plus a broker OrderRequest.

    Anything the model got wrong or left out raises here, before the gate sees
    it. Missing numbers are never defaulted to something permissive.
    """
    symbol = str(p.get("symbol") or "").upper().strip()
    if not symbol:
        raise ValueError("proposal has no symbol")

    side = str(p.get("side") or "").lower()
    if side not in {"buy", "sell", "buy_to_open", "sell_to_open",
                    "buy_to_close", "sell_to_close"}:
        raise ValueError(f"unknown side {side!r} for {symbol}")

    try:
        quantity = float(p["quantity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"bad quantity for {symbol}") from exc
    if quantity <= 0:
        raise ValueError(f"non-positive quantity for {symbol}")

    quote = next((q for q in quotes if q.symbol == symbol), None)
    if quote is None:
        raise ValueError(f"no quote available for {symbol}")

    asset_class = str(p.get("asset_class") or "equity")
    is_closing = side.endswith("_to_close") or (
        side == "sell" and any(pos.symbol == symbol for pos in account.positions)
    )

    limit_price = p.get("limit_price")
    limit_price = float(limit_price) if limit_price is not None else quote.mid
    multiplier = 100 if asset_class == "option" else 1
    notional = abs(quantity * limit_price * multiplier)

    # If the model does not state a max loss we assume the worst the structure
    # can do, which for anything we permit is the full notional. Guessing lower
    # would let an oversized position through the sizing check.
    max_loss = p.get("max_loss")
    max_loss = float(max_loss) if max_loss is not None else notional

    proposal = G.Proposal(
        symbol=symbol,
        asset_class=asset_class,
        side=side,
        quantity=quantity,
        notional=notional,
        max_loss=max_loss,
        setup_tag=str(p.get("setup_tag") or "unspecified"),
        confidence=_clamp(float(p.get("confidence", 0.5)), 0.0, 1.0),
        thesis=str(p.get("thesis") or "").strip() or "(none given)",
        expected_edge_pct=float(p.get("expected_edge_pct", 0.0)),
        est_spread_pct=quote.spread_pct,
        est_fees=float(p.get("est_fees", 0.0)),
        dte=int(p["dte"]) if p.get("dte") is not None else None,
        defined_risk=bool(p.get("defined_risk", asset_class != "option")),
        is_closing=is_closing,
        data_age_seconds=quote.age_seconds(now),
    )

    order = OrderRequest(
        symbol=symbol,
        asset_class=asset_class,
        side=side,
        quantity=quantity,
        order_type="limit",
        limit_price=limit_price,
        time_in_force="day",
    )
    return proposal, order


def _account_state(
    account: Account, sod_equity: float, peak: float,
    store: Store, day_start: str, cycle_id: str,
) -> G.AccountState:
    return G.AccountState(
        equity=account.equity,
        cash=account.cash,
        buying_power=account.buying_power,
        settled_cash=account.settled_cash,
        start_of_day_equity=sod_equity,
        peak_equity=peak,
        realized_pnl_today=0.0,
        unrealized_pnl_today=account.unrealized_pnl,
        deployed_notional=account.deployed_notional,
        symbol_exposure=account.exposure_by_symbol(),
        trades_today=store.trades_today(day_start),
        trades_this_cycle=0,
        symbols_closed_today=store.symbols_closed_today(day_start),
    )


def _with_cycle_count(state: G.AccountState, n: int) -> G.AccountState:
    from dataclasses import replace

    return replace(state, trades_this_cycle=n)


def _min_viable_notional(cfg: Config, quotes: list[Quote]) -> float:
    """Smallest order that could plausibly be placed.

    Fractional shares make any dollar amount tradeable in equities, so the floor
    is the risk budget itself. Options come in $100-multiplier contracts, so the
    floor is one contract of the cheapest thing quoted.
    """
    budget = 1.0
    if any(s for s in cfg.agent.setups if "spread" in s or "condor" in s):
        cheapest = min((q.mid for q in quotes if q.mid > 0), default=0.0)
        budget = max(budget, cheapest * 100 * 0.01)
    return budget


def _fetch_quotes(broker: Broker, universe: tuple[str, ...], store: Store) -> list[Quote]:
    out: list[Quote] = []
    for symbol in universe:
        try:
            out.append(broker.quote(symbol))
        except AuthExpired:
            raise
        except BrokerError as exc:
            # One bad symbol must not abort the cycle; it just means the agent
            # cannot trade that name, and the gate blocks it for lack of a quote.
            store.log("warn", "quote_failed", f"{symbol}: {exc}")
    return out


def _safe(fn, default, store: Store, kind: str):
    try:
        return fn()
    except AuthExpired:
        raise
    except Exception as exc:  # noqa: BLE001 - non-critical read
        store.log("warn", kind, str(exc))
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
