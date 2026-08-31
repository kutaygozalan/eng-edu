"""Bounded context assembly.

The agent's memory grows every day; the context window does not. So this module
decides what a cycle actually gets to see, under a hard budget.

Priority order, highest first:
  1. Process rules      - hard constraints. Always included, never truncated.
  2. Calibration        - small, and the most actionable feedback available.
  3. Setup expectancy   - compact table; posterior only, never the raw mean.
  4. Relevant lessons   - ranked, capped.
  5. Similar past trades - retrieval by setup, capped.

The posterior-only rule matters. If the agent sees "observed +18% over 5 trades"
it will reason about the 18%; showing only the shrunk figure means the number it
reasons about is already the honest one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_LESSON_CHARS = 4000
MAX_SIMILAR = 5


@dataclass
class CycleContext:
    """Everything the model sees, split by cache stability.

    `stable` changes rarely and sits behind a cache breakpoint. `volatile` is
    per-cycle. Mixing them would invalidate the cache on every single call - a
    real cost when this runs 18 times a day.
    """

    stable: str
    volatile: str


def build_stable_block(
    *,
    lessons: list[dict],
    setup_stats: list[dict],
    calibration: list[dict],
    universe: tuple[str, ...],
    setups: tuple[str, ...],
    limits_summary: dict[str, Any],
) -> str:
    """The slowly-changing half: accumulated knowledge and standing rules.

    Rewritten once a night by the review, so it stays cache-stable across a
    whole trading day.
    """
    parts: list[str] = []

    process = [l for l in lessons if l["scope"] == "process"]
    if process:
        parts.append(
            "## Standing rules (learned from your own past mistakes)\n"
            "These are non-negotiable. Each was written after you violated it.\n"
            + "\n".join(f"- {l['text']}" for l in process)
        )

    if calibration:
        rows = "\n".join(
            f"  when you said {c['mean_stated']:.0%} confident (n={c['n']}), "
            f"you were right {c['realized_rate']:.0%} of the time"
            for c in sorted(calibration, key=lambda c: c["bucket"])
            if c["n"] >= 5
        )
        if rows:
            parts.append(
                "## Your calibration history\n"
                "Adjust your stated confidence to match reality:\n" + rows
            )

    actionable = [s for s in setup_stats if s["n"] >= 5]
    if actionable:
        rows = "\n".join(
            f"  {s['setup_tag']:<24} n={s['n']:<4} "
            f"expectancy {s['posterior_mean_pct']:+.2%}"
            + ("  [BLOCKED - do not propose]" if s["blocked"] else "")
            + ("" if s["n"] >= 30 else "  [too few samples to act on]")
            for s in sorted(setup_stats, key=lambda s: -s["n"])
            if s["n"] >= 5
        )
        parts.append(
            "## Realized expectancy by setup\n"
            "These are SHRUNK toward zero edge - a small sample of wins does not "
            "make a strategy. Setups marked [too few samples] must not influence "
            "your sizing.\n" + rows
        )

    other = [l for l in lessons if l["scope"] != "process"]
    if other:
        block, used = [], 0
        for l in other:
            line = f"- ({l['scope']}, n={l['evidence_n']}) {l['text']}"
            if used + len(line) > MAX_LESSON_CHARS:
                break
            block.append(line)
            used += len(line)
        if block:
            parts.append("## Observations from your journal\n" + "\n".join(block))

    parts.append(
        "## Mandate\n"
        f"Universe: {', '.join(universe) if universe else '(unrestricted)'}\n"
        f"Permitted setups: {', '.join(setups) if setups else '(none configured)'}\n"
        f"Hard limits enforced in code: {json.dumps(limits_summary, sort_keys=True)}"
    )
    return "\n\n".join(parts)


def build_volatile_block(
    *,
    now_iso: str,
    session: dict[str, Any],
    account: dict[str, Any],
    positions: list[dict],
    quotes: list[dict],
    todays_decisions: list[dict],
    similar: list[dict],
    open_orders: list[dict],
) -> str:
    """The per-cycle half: what is true right now."""
    parts: list[str] = [
        f"## Now\n{now_iso}\n"
        f"minutes since open: {session.get('minutes_since_open', 0):.0f}, "
        f"minutes to close: {session.get('minutes_until_close', 0):.0f}"
        + ("  (EARLY CLOSE TODAY)" if session.get("is_early_close") else "")
    ]

    parts.append(
        "## Account\n"
        f"equity ${account.get('equity', 0):,.2f} | "
        f"settled cash ${account.get('settled_cash', 0):,.2f} | "
        f"buying power ${account.get('buying_power', 0):,.2f}\n"
        f"deployed ${account.get('deployed_notional', 0):,.2f} | "
        f"unrealized P&L ${account.get('unrealized_pnl', 0):+,.2f} | "
        f"realized today ${account.get('realized_pnl_today', 0):+,.2f}\n"
        f"trades placed today: {account.get('trades_today', 0)}"
    )

    if positions:
        rows = "\n".join(
            f"  {p['symbol']:<12} {p['quantity']:>8.2f} @ ${p['avg_price']:.2f}  "
            f"value ${p['market_value']:,.2f}  P&L ${p['unrealized_pnl']:+,.2f}"
            for p in positions
        )
        parts.append(f"## Open positions ({len(positions)})\n{rows}")
    else:
        parts.append("## Open positions\n  (none)")

    if open_orders:
        parts.append(
            "## Unfilled orders\n"
            + "\n".join(f"  {o['broker_order_id']} {o['status']}" for o in open_orders)
            + "\nDo not duplicate these."
        )

    if quotes:
        rows = "\n".join(
            f"  {q['symbol']:<8} bid {q['bid']:>9.2f}  ask {q['ask']:>9.2f}  "
            f"last {q['last']:>9.2f}  spread {q['spread_pct']:.2%}"
            + ("  [STALE]" if q.get("age_seconds", 0) > 120 else "")
            for q in quotes
        )
        parts.append(f"## Quotes\n{rows}")

    if todays_decisions:
        rows = "\n".join(
            f"  {d['symbol']} {d['side']} {d['setup_tag']} -> {d['gate_verdict']}"
            + (f" ({', '.join(json.loads(d['gate_reasons'] or '[]'))})"
               if d["gate_verdict"] == "reject" else "")
            for d in todays_decisions
        )
        parts.append(
            f"## What you already decided today\n{rows}\n"
            "Do not re-propose something the gate already rejected for a reason "
            "that still holds."
        )

    if similar:
        rows = "\n".join(
            f"  {s['symbol']:<8} {s['setup_tag']:<22} "
            f"stated {float(s['confidence']):.0%} -> {float(s['pnl_pct']):+.2%} "
            f"({s['exit_reason']})\n      thesis was: {s['thesis'][:140]}"
            for s in similar[:MAX_SIMILAR]
        )
        parts.append(f"## How similar trades actually worked out\n{rows}")

    return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Rough sizing guard. Not exact - just enough to catch runaway growth."""
    return len(text) // 4
