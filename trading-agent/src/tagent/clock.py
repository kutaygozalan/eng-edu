"""Market clock.

The scheduler fires on wall-clock time; this module decides whether a given
moment is actually tradeable. Kept dependency-free and pure so it is trivially
testable - a bug here means the agent trades into a closed or half-day market.

US holiday dates are hardcoded rather than computed. Half-days matter as much as
closures: an early close at 13:00 ET turns the normal end-of-day blackout into a
window that has already passed by the time a naive scheduler notices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# NYSE full closures.
HOLIDAYS_2026: frozenset[date] = frozenset({
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
})

HOLIDAYS_2027: frozenset[date] = frozenset({
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})

HOLIDAYS = HOLIDAYS_2026 | HOLIDAYS_2027

# 1:00pm ET closes.
EARLY_CLOSES: frozenset[date] = frozenset({
    date(2026, 11, 27),  # day after Thanksgiving
    date(2026, 12, 24),  # Christmas Eve
    date(2027, 11, 26),
})


@dataclass(frozen=True)
class MarketSession:
    is_open: bool
    minutes_since_open: float
    minutes_until_close: float
    session_date: date
    is_early_close: bool
    reason: str = ""


def _et(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def close_time_for(d: date) -> time:
    return EARLY_CLOSE if d in EARLY_CLOSES else REGULAR_CLOSE


def session(now: datetime) -> MarketSession:
    """Classify `now` (any tz, or naive-as-UTC) against the US equity session."""
    local = _et(now)
    d = local.date()
    early = d in EARLY_CLOSES
    closes_at = close_time_for(d)

    if not is_trading_day(d):
        reason = "weekend" if d.weekday() >= 5 else "market holiday"
        return MarketSession(False, 0.0, 0.0, d, early, reason)

    open_dt = local.replace(
        hour=REGULAR_OPEN.hour, minute=REGULAR_OPEN.minute,
        second=0, microsecond=0,
    )
    close_dt = local.replace(
        hour=closes_at.hour, minute=closes_at.minute, second=0, microsecond=0
    )

    since_open = (local - open_dt).total_seconds() / 60.0
    until_close = (close_dt - local).total_seconds() / 60.0
    is_open = since_open >= 0 and until_close > 0

    reason = ""
    if not is_open:
        reason = "before open" if since_open < 0 else "after close"

    return MarketSession(
        is_open=is_open,
        minutes_since_open=max(0.0, since_open),
        minutes_until_close=max(0.0, until_close),
        session_date=d,
        is_early_close=early,
        reason=reason,
    )


def session_day_start_utc(now: datetime) -> str:
    """ISO timestamp of 00:00 ET on the current session date, in UTC.

    Used to scope 'today' queries. Calendar-day boundaries in ET, not UTC:
    a UTC day boundary falls at 8pm ET and would split a session in half.
    """
    local = _et(now)
    midnight_et = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_et.astimezone(timezone.utc).isoformat()


def _next_session_open(local: datetime) -> datetime:
    """First moment of the next open session, at or after `local` (ET-aware)."""
    d = local.date()
    open_today = local.replace(
        hour=REGULAR_OPEN.hour, minute=REGULAR_OPEN.minute, second=0, microsecond=0
    )
    if is_trading_day(d) and local < open_today:
        return open_today
    for offset in range(1, 11):
        nd = d + timedelta(days=offset)
        if is_trading_day(nd):
            return datetime.combine(nd, REGULAR_OPEN, tzinfo=ET)
    raise RuntimeError("no trading session found within 10 days")


def next_run_after(now: datetime, interval_minutes: int) -> datetime:
    """Next scheduled wake-up, skipping non-tradeable time.

    During an open session this is simply `now + interval`, unless that lands
    past the close - in which case it rolls to the next session's open. Early
    closes are handled by `session()`, so a 13:00 close does not produce a
    phantom 15:30 wake-up.
    """
    local = _et(now)
    if session(local).is_open:
        candidate = local + timedelta(minutes=interval_minutes)
        if session(candidate).is_open:
            return candidate.astimezone(timezone.utc)
        return _next_session_open(candidate).astimezone(timezone.utc)
    return _next_session_open(local).astimezone(timezone.utc)
