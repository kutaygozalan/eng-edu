"""Market clock tests. A bug here trades into a closed or half-day market."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tagent.clock import (  # noqa: E402
    ET, close_time_for, is_trading_day, next_run_after, session,
    session_day_start_utc,
)


def et(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def test_midsession_is_open():
    s = session(et(2026, 9, 15, 11, 0))
    assert s.is_open
    assert s.minutes_since_open == 90
    assert s.minutes_until_close == 300


def test_before_open_closed():
    s = session(et(2026, 9, 15, 9, 0))
    assert not s.is_open and s.reason == "before open"


def test_after_close_closed():
    s = session(et(2026, 9, 15, 16, 30))
    assert not s.is_open and s.reason == "after close"


def test_exact_open_is_open():
    assert session(et(2026, 9, 15, 9, 30)).is_open


def test_exact_close_is_closed():
    """16:00:00 is the close, not a tradeable instant."""
    assert not session(et(2026, 9, 15, 16, 0)).is_open


def test_weekend_closed():
    assert not session(et(2026, 9, 19, 11, 0)).is_open   # Saturday
    assert not session(et(2026, 9, 20, 11, 0)).is_open   # Sunday


def test_holiday_closed():
    s = session(et(2026, 11, 26, 11, 0))                 # Thanksgiving
    assert not s.is_open and s.reason == "market holiday"


def test_early_close_shortens_session():
    """13:00 close: 12:50 must show 10 minutes left, not 190."""
    s = session(et(2026, 11, 27, 12, 50))
    assert s.is_open and s.is_early_close
    assert s.minutes_until_close == 10


def test_early_close_afternoon_is_closed():
    assert not session(et(2026, 11, 27, 14, 0)).is_open


def test_utc_input_converted():
    """15:00 UTC is 11:00 ET in September - mid-session."""
    assert session(datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)).is_open


def test_naive_datetime_treated_as_utc():
    assert session(datetime(2026, 9, 15, 15, 0)).is_open


def test_dst_boundary_handled():
    """November: 15:00 UTC is 10:00 ET, still open."""
    assert session(datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)).is_open
    # ...but 21:30 UTC is 16:30 ET, closed.
    assert not session(datetime(2026, 11, 5, 21, 30, tzinfo=timezone.utc)).is_open


def test_close_time_lookup():
    assert close_time_for(date(2026, 9, 15)).hour == 16
    assert close_time_for(date(2026, 12, 24)).hour == 13


def test_is_trading_day():
    assert is_trading_day(date(2026, 9, 15))
    assert not is_trading_day(date(2026, 9, 19))
    assert not is_trading_day(date(2026, 12, 25))


# ------------------------------------------------------------- scheduling --

def test_next_run_within_session():
    nxt = next_run_after(et(2026, 9, 15, 11, 0), 20)
    assert nxt.astimezone(ET).hour == 11 and nxt.astimezone(ET).minute == 20


def test_next_run_rolls_past_close_to_next_open():
    nxt = next_run_after(et(2026, 9, 15, 15, 50), 30).astimezone(ET)
    assert nxt.date() == date(2026, 9, 16)
    assert (nxt.hour, nxt.minute) == (9, 30)


def test_next_run_from_closed_market_is_next_open():
    nxt = next_run_after(et(2026, 9, 15, 6, 0), 15).astimezone(ET)
    assert nxt.date() == date(2026, 9, 15) and nxt.hour == 9


def test_next_run_skips_weekend():
    nxt = next_run_after(et(2026, 9, 18, 17, 0), 15).astimezone(ET)  # Friday evening
    assert nxt.date() == date(2026, 9, 21)                           # Monday


def test_next_run_skips_holiday():
    nxt = next_run_after(et(2026, 11, 25, 17, 0), 15).astimezone(ET)
    assert nxt.date() == date(2026, 11, 27)   # skips Thanksgiving


def test_next_run_respects_early_close():
    """A 15-minute step from 12:55 on a half day must not schedule 13:10."""
    nxt = next_run_after(et(2026, 11, 27, 12, 55), 15).astimezone(ET)
    assert nxt.date() > date(2026, 11, 27)


def test_day_start_uses_eastern_midnight():
    """A UTC day boundary falls at 8pm ET and would split a session."""
    start = session_day_start_utc(et(2026, 9, 15, 11, 0))
    assert datetime.fromisoformat(start).astimezone(ET).hour == 0
    assert datetime.fromisoformat(start).astimezone(ET).date() == date(2026, 9, 15)


def test_day_start_stable_across_a_session():
    a = session_day_start_utc(et(2026, 9, 15, 9, 35))
    b = session_day_start_utc(et(2026, 9, 15, 15, 55))
    assert a == b
