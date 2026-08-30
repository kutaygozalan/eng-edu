"""The nightly review - the agent's trading journal.

This is what turns "an LLM that places orders" into "a trader who gets better."
It runs once after the close and does four things:

  1. Recompute realized expectancy per setup, SHRUNK toward zero edge.
  2. Update the calibration curve (stated confidence vs. realized hit rate).
  3. Decide which setups to block outright.
  4. Hand the LLM a bounded, evidence-linked packet so it can write lessons.

The statistics here exist to stop the agent believing itself. With ~10-40 trades
a week, an unshrunk win-rate is mostly noise, and an agent that reads its own
noisy results as signal will lever into them. Everything below is built to make
that specific failure hard.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Pseudo-observations of zero edge mixed into every setup's history. With k=20,
# five profitable trades move the posterior by a fifth of what the raw mean
# suggests. This is the main defense against learning noise.
PRIOR_STRENGTH = 20.0
PRIOR_MEAN_PCT = 0.0

# An 'edge' lesson may not influence sizing below this sample count. Process
# lessons have no such threshold - a rule violation is knowable from one case.
MIN_N_FOR_EDGE_CLAIM = 30

# A setup is blocked when we are reasonably confident it is a loser: the
# posterior mean is negative by more than two standard errors.
BLOCK_SE_MULTIPLE = 2.0
MIN_N_TO_BLOCK = 15

CALIBRATION_BUCKETS = [
    (0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01),
]


@dataclass
class SetupStat:
    setup_tag: str
    regime_tag: str
    n: int
    wins: int
    observed_mean_pct: float
    observed_sd_pct: float
    posterior_mean_pct: float
    posterior_se_pct: float
    total_pnl: float
    blocked: bool

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def actionable(self) -> bool:
        """Whether this stat may influence position sizing at all."""
        return self.n >= MIN_N_FOR_EDGE_CLAIM


@dataclass
class CalibrationBucket:
    bucket: str
    n: int
    wins: int
    mean_stated: float
    realized_rate: float

    @property
    def gap(self) -> float:
        """Positive = overconfident. The number the agent needs to see."""
        return self.mean_stated - self.realized_rate


def shrink(observed_mean: float, observed_sd: float, n: int) -> tuple[float, float]:
    """James-Stein style shrinkage toward a zero-edge prior.

    Returns (posterior_mean, posterior_standard_error).

    The posterior is a precision-weighted blend of "no edge" and what we
    actually saw. Small n keeps us near zero no matter how good the sample
    looked, which is the entire point.
    """
    if n <= 0:
        return PRIOR_MEAN_PCT, 0.0
    posterior_mean = (PRIOR_STRENGTH * PRIOR_MEAN_PCT + n * observed_mean) / (
        PRIOR_STRENGTH + n
    )
    se = (observed_sd / math.sqrt(n)) if (n > 1 and observed_sd > 0) else 0.0
    # The blend shrinks the standard error by the same weight as the mean.
    posterior_se = se * (n / (PRIOR_STRENGTH + n))
    return posterior_mean, posterior_se


def compute_setup_stats(rows: list[dict]) -> list[SetupStat]:
    """Aggregate closed outcomes into per-(setup, regime) statistics.

    `rows` are joined decision+outcome dicts with at least:
      setup_tag, regime_tag, pnl_pct, pnl, was_win
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        # Every trade lands in both its specific regime bucket and the
        # all-regimes rollup, so a setup accumulates evidence faster in
        # aggregate than it does in any single regime.
        for regime in (r.get("regime_tag") or "unknown", "*"):
            grouped.setdefault((r["setup_tag"], regime), []).append(r)

    stats: list[SetupStat] = []
    for (setup, regime), items in sorted(grouped.items()):
        returns = [float(i["pnl_pct"]) for i in items]
        n = len(returns)
        mean = statistics.fmean(returns) if n else 0.0
        sd = statistics.stdev(returns) if n > 1 else 0.0
        post_mean, post_se = shrink(mean, sd, n)
        wins = sum(1 for i in items if int(i["was_win"]) == 1)
        total_pnl = sum(float(i["pnl"]) for i in items)

        blocked = (
            n >= MIN_N_TO_BLOCK
            and post_mean < 0
            and abs(post_mean) > BLOCK_SE_MULTIPLE * max(post_se, 1e-9)
        )

        stats.append(
            SetupStat(
                setup_tag=setup,
                regime_tag=regime,
                n=n,
                wins=wins,
                observed_mean_pct=mean,
                observed_sd_pct=sd,
                posterior_mean_pct=post_mean,
                posterior_se_pct=post_se,
                total_pnl=total_pnl,
                blocked=blocked,
            )
        )
    return stats


def compute_calibration(rows: list[dict]) -> list[CalibrationBucket]:
    """Bucket stated confidence against realized outcomes.

    This is the most reliably learnable signal the agent has. Directional edge
    is hard and may not exist; knowing that your "80% confident" trades win 55%
    of the time is a correction you can actually apply.
    """
    buckets: list[CalibrationBucket] = []
    for lo, hi in CALIBRATION_BUCKETS:
        items = [r for r in rows if lo <= float(r["confidence"]) < hi]
        if not items:
            continue
        n = len(items)
        wins = sum(1 for i in items if int(i["was_win"]) == 1)
        buckets.append(
            CalibrationBucket(
                bucket=f"{lo:.1f}-{hi:.1f}",
                n=n,
                wins=wins,
                mean_stated=statistics.fmean(float(i["confidence"]) for i in items),
                realized_rate=wins / n,
            )
        )
    return buckets


def rejection_summary(rejections: list[dict], top_n: int = 8) -> list[tuple[str, int]]:
    """Which gate rules fired most often.

    A rule that fires constantly is a signal about the agent, not the rule: it
    means the strategy layer keeps proposing something it should have known was
    disallowed. These become process lessons.
    """
    counts: dict[str, int] = {}
    for r in rejections:
        for reason in json.loads(r.get("gate_reasons") or "[]"):
            counts[reason] = counts.get(reason, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]


@dataclass
class ReviewPacket:
    """Bounded input to the LLM's journal-writing step.

    Bounded is the operative word: this is assembled fresh each night and never
    grows without limit, because an unbounded journal eventually costs more than
    it teaches and pushes out the parts that matter.
    """

    as_of: str
    closed_trades: list[dict]
    setup_stats: list[SetupStat]
    calibration: list[CalibrationBucket]
    top_rejections: list[tuple[str, int]]
    active_lessons: list[dict]
    equity_start: float
    equity_end: float

    def to_prompt_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "session_pnl": round(self.equity_end - self.equity_start, 2),
            "session_return_pct": round(
                (self.equity_end / self.equity_start - 1) if self.equity_start else 0, 5
            ),
            "closed_trades": [
                {
                    "symbol": t["symbol"],
                    "setup": t["setup_tag"],
                    "regime": t.get("regime_tag"),
                    "stated_confidence": round(float(t["confidence"]), 2),
                    "pnl": round(float(t["pnl"]), 2),
                    "pnl_pct": round(float(t["pnl_pct"]), 4),
                    "holding_days": round(float(t["holding_days"]), 2),
                    "exit_reason": t["exit_reason"],
                    "slippage": round(float(t.get("slippage") or 0), 4),
                    "thesis": t["thesis"],
                }
                for t in self.closed_trades
            ],
            "setup_expectancy": [
                {
                    "setup": s.setup_tag,
                    "regime": s.regime_tag,
                    "n": s.n,
                    "win_rate": round(s.win_rate, 3),
                    "observed_mean_pct": round(s.observed_mean_pct, 4),
                    "posterior_mean_pct": round(s.posterior_mean_pct, 4),
                    "actionable": s.actionable,
                    "blocked": s.blocked,
                }
                for s in self.setup_stats
                if s.regime_tag == "*"
            ],
            "calibration": [
                {
                    "confidence_bucket": c.bucket,
                    "n": c.n,
                    "stated": round(c.mean_stated, 3),
                    "realized": round(c.realized_rate, 3),
                    "overconfidence_gap": round(c.gap, 3),
                }
                for c in self.calibration
            ],
            "most_frequent_gate_rejections": [
                {"reason": r, "count": c} for r, c in self.top_rejections
            ],
            "active_lessons": [
                {"id": l["id"], "scope": l["scope"], "text": l["text"], "n": l["evidence_n"]}
                for l in self.active_lessons
            ],
        }


def validate_proposed_lesson(lesson: dict, stats: list[SetupStat]) -> tuple[bool, str]:
    """Gate on what the LLM is allowed to write into long-term memory.

    An agent left to journal freely will write "I should have been more
    aggressive" after a good day and lever itself into the next drawdown. So
    edge claims must clear an evidence bar; process claims need not.
    """
    scope = lesson.get("scope")
    text = (lesson.get("text") or "").strip()

    if scope not in {"process", "edge", "regime"}:
        return False, f"unknown scope {scope!r}"
    if len(text) < 12:
        return False, "lesson text too short to be checkable"
    if len(text) > 400:
        return False, "lesson text too long; must be a single actionable rule"

    if scope == "process":
        # Deterministic mistakes are learnable from a single occurrence.
        return True, ""

    setup = lesson.get("setup_tag")
    if not setup:
        return False, "edge/regime lessons must name a setup_tag"

    match = next(
        (s for s in stats if s.setup_tag == setup and s.regime_tag == "*"), None
    )
    if match is None:
        return False, f"no recorded outcomes for setup {setup!r}"
    if match.n < MIN_N_FOR_EDGE_CLAIM:
        return False, (
            f"setup {setup!r} has n={match.n}, below the {MIN_N_FOR_EDGE_CLAIM} "
            "required before an edge claim may be recorded"
        )
    return True, ""


def should_retire(lesson: dict, stats: list[SetupStat], now: datetime) -> tuple[bool, str]:
    """Retire lessons whose supporting evidence has gone away.

    A journal that only ever grows becomes a set of superstitions. Edge lessons
    are retired once the setup they describe is no longer profitable in the
    posterior; process lessons persist until explicitly superseded.
    """
    if lesson.get("scope") != "edge":
        return False, ""
    setup = lesson.get("setup_tag")
    match = next((s for s in stats if s.setup_tag == setup and s.regime_tag == "*"), None)
    if match is None:
        return False, ""
    if match.posterior_mean_pct <= 0:
        return True, (
            f"posterior expectancy for {setup} fell to "
            f"{match.posterior_mean_pct:.4f}; claim no longer supported"
        )
    return False, ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
