"""Tests for the learning engine.

The thing being tested is not "does it compute a mean" but "does it refuse to
believe a small sample." Every test below is really asking: can the agent talk
itself into a bad idea?
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tagent.memory.review import (  # noqa: E402
    MIN_N_FOR_EDGE_CLAIM, PRIOR_STRENGTH, CalibrationBucket, compute_calibration,
    compute_setup_stats, rejection_summary, shrink, should_retire,
    validate_proposed_lesson,
)

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


def trade(setup="wheel", regime="calm", pnl_pct=0.02, conf=0.7, pnl=None):
    return {
        "setup_tag": setup, "regime_tag": regime, "pnl_pct": pnl_pct,
        "pnl": pnl if pnl is not None else pnl_pct * 1000,
        "was_win": 1 if pnl_pct > 0 else 0, "confidence": conf,
    }


# ------------------------------------------------------------- shrinkage ---

def test_shrinkage_pulls_small_samples_toward_zero():
    """Four lucky trades must not read as a 20% edge."""
    observed = 0.20
    post, _ = shrink(observed, 0.05, n=4)
    assert 0 < post < observed / 4


def test_shrinkage_relaxes_as_evidence_accumulates():
    observed = 0.10
    small, _ = shrink(observed, 0.05, n=5)
    large, _ = shrink(observed, 0.05, n=200)
    assert small < large < observed
    assert large == pytest.approx(observed * 200 / (200 + PRIOR_STRENGTH), rel=1e-6)


def test_shrinkage_is_symmetric_for_losses():
    pos, _ = shrink(0.10, 0.05, n=10)
    neg, _ = shrink(-0.10, 0.05, n=10)
    assert pos == pytest.approx(-neg)


def test_shrinkage_handles_zero_samples():
    assert shrink(0.5, 0.1, n=0) == (0.0, 0.0)


def test_shrinkage_never_exceeds_observed():
    for n in (1, 5, 50, 500):
        post, _ = shrink(0.08, 0.04, n)
        assert abs(post) <= 0.08


# ---------------------------------------------------------- setup stats ----

def test_small_winning_sample_is_not_actionable():
    stats = compute_setup_stats([trade(pnl_pct=0.15) for _ in range(5)])
    rollup = next(s for s in stats if s.regime_tag == "*")
    assert rollup.win_rate == 1.0          # perfect record...
    assert not rollup.actionable            # ...and still may not size on it
    assert rollup.posterior_mean_pct < rollup.observed_mean_pct / 4


def test_large_sample_becomes_actionable():
    stats = compute_setup_stats([trade(pnl_pct=0.03) for _ in range(MIN_N_FOR_EDGE_CLAIM)])
    rollup = next(s for s in stats if s.regime_tag == "*")
    assert rollup.actionable


def test_consistent_loser_gets_blocked():
    rows = [trade(pnl_pct=-0.05) for _ in range(40)]
    stats = compute_setup_stats(rows)
    rollup = next(s for s in stats if s.regime_tag == "*")
    assert rollup.blocked


def test_small_losing_sample_not_blocked():
    """Three bad trades is not evidence; blocking on it would thrash."""
    stats = compute_setup_stats([trade(pnl_pct=-0.05) for _ in range(3)])
    rollup = next(s for s in stats if s.regime_tag == "*")
    assert not rollup.blocked


def test_noisy_losing_sample_not_blocked():
    """Wide dispersion means we cannot distinguish the mean from zero."""
    rows = [trade(pnl_pct=p) for p in ([0.40, -0.45] * 10)]
    stats = compute_setup_stats(rows)
    rollup = next(s for s in stats if s.regime_tag == "*")
    assert not rollup.blocked


def test_trades_land_in_both_regime_and_rollup():
    stats = compute_setup_stats([trade(regime="calm") for _ in range(6)])
    tags = {s.regime_tag for s in stats}
    assert tags == {"calm", "*"}
    assert all(s.n == 6 for s in stats)


def test_regimes_are_tracked_separately():
    rows = [trade(regime="calm", pnl_pct=0.05) for _ in range(10)]
    rows += [trade(regime="volatile", pnl_pct=-0.05) for _ in range(10)]
    stats = {(s.setup_tag, s.regime_tag): s for s in compute_setup_stats(rows)}
    assert stats[("wheel", "calm")].posterior_mean_pct > 0
    assert stats[("wheel", "volatile")].posterior_mean_pct < 0
    assert stats[("wheel", "*")].n == 20


def test_missing_regime_falls_back_to_unknown():
    rows = [{**trade(), "regime_tag": None}]
    stats = compute_setup_stats(rows)
    assert any(s.regime_tag == "unknown" for s in stats)


# --------------------------------------------------------- calibration -----

def test_calibration_detects_overconfidence():
    """Says 90%, wins 30% of the time."""
    rows = [trade(conf=0.9, pnl_pct=0.01) for _ in range(3)]
    rows += [trade(conf=0.9, pnl_pct=-0.01) for _ in range(7)]
    bucket = next(b for b in compute_calibration(rows) if b.bucket == "0.9-1.0")
    assert bucket.realized_rate == pytest.approx(0.3)
    assert bucket.gap > 0.5


def test_calibration_detects_underconfidence():
    rows = [trade(conf=0.55, pnl_pct=0.01) for _ in range(9)]
    rows += [trade(conf=0.55, pnl_pct=-0.01)]
    bucket = next(b for b in compute_calibration(rows) if b.bucket == "0.5-0.6")
    assert bucket.gap < -0.3


def test_empty_buckets_omitted():
    assert compute_calibration([trade(conf=0.65)]) == [
        b for b in compute_calibration([trade(conf=0.65)]) if b.n > 0
    ]
    assert len(compute_calibration([trade(conf=0.65)])) == 1


# ------------------------------------------------------- lesson gating -----

def test_process_lesson_accepted_without_evidence():
    """A rule violation is knowable from one occurrence."""
    ok, why = validate_proposed_lesson(
        {"scope": "process", "text": "Never submit an order in the final 20 minutes."},
        stats=[],
    )
    assert ok, why


def test_edge_lesson_rejected_without_enough_samples():
    stats = compute_setup_stats([trade(pnl_pct=0.2) for _ in range(6)])
    ok, why = validate_proposed_lesson(
        {"scope": "edge", "setup_tag": "wheel", "text": "The wheel is highly profitable."},
        stats,
    )
    assert not ok
    assert "below the" in why


def test_edge_lesson_accepted_with_enough_samples():
    stats = compute_setup_stats(
        [trade(pnl_pct=0.02) for _ in range(MIN_N_FOR_EDGE_CLAIM + 5)]
    )
    ok, why = validate_proposed_lesson(
        {"scope": "edge", "setup_tag": "wheel", "text": "Wheel entries hold up in calm regimes."},
        stats,
    )
    assert ok, why


def test_edge_lesson_must_name_a_setup():
    ok, _ = validate_proposed_lesson(
        {"scope": "edge", "text": "Trading is good when the market goes up."}, []
    )
    assert not ok


def test_edge_lesson_for_unknown_setup_rejected():
    ok, why = validate_proposed_lesson(
        {"scope": "edge", "setup_tag": "ghost", "text": "Ghost setup prints money."},
        compute_setup_stats([trade()]),
    )
    assert not ok
    assert "no recorded outcomes" in why


def test_vague_and_rambling_lessons_rejected():
    assert not validate_proposed_lesson({"scope": "process", "text": "do better"}, [])[0]
    assert not validate_proposed_lesson({"scope": "process", "text": "x" * 500}, [])[0]


def test_unknown_scope_rejected():
    ok, _ = validate_proposed_lesson({"scope": "vibes", "text": "feels bullish today"}, [])
    assert not ok


# ------------------------------------------------------------ retirement ---

def test_edge_lesson_retired_when_expectancy_turns_negative():
    stats = compute_setup_stats([trade(pnl_pct=-0.03) for _ in range(40)])
    retire, why = should_retire(
        {"scope": "edge", "setup_tag": "wheel", "text": "wheel works"}, stats, NOW
    )
    assert retire
    assert "no longer supported" in why


def test_profitable_edge_lesson_kept():
    stats = compute_setup_stats([trade(pnl_pct=0.03) for _ in range(40)])
    retire, _ = should_retire(
        {"scope": "edge", "setup_tag": "wheel", "text": "wheel works"}, stats, NOW
    )
    assert not retire


def test_process_lessons_never_auto_retired():
    """Process rules outlive the sample that produced them."""
    stats = compute_setup_stats([trade(pnl_pct=-0.5) for _ in range(50)])
    retire, _ = should_retire({"scope": "process", "text": "no 0DTE"}, stats, NOW)
    assert not retire


# ------------------------------------------------------------ rejections ---

def test_rejection_summary_ranks_by_frequency():
    rows = [
        {"gate_reasons": '["cost_exceeds_edge", "spread_too_wide"]'},
        {"gate_reasons": '["cost_exceeds_edge"]'},
        {"gate_reasons": '["max_trades_per_day"]'},
    ]
    top = rejection_summary(rows)
    assert top[0] == ("cost_exceeds_edge", 2)
    assert dict(top)["spread_too_wide"] == 1


def test_rejection_summary_tolerates_empty():
    assert rejection_summary([]) == []
    assert rejection_summary([{"gate_reasons": None}]) == []
