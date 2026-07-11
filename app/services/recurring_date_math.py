"""
Date math for recurring shared expenses — a deliberate, close port of
the personal ledger's client-side engine (src/lib/recurring.ts in the
frontend repo), not a reinvention. Two schedules should behave
identically whether they're personal or shared; porting the exact
same anchor-preserving month arithmetic means "rent on the 31st"
behaves the same way for a personal recurring transaction and a
shared recurring expense, rather than two subtly different
interpretations of "monthly."

Kept as pure functions, no I/O, no ORM — the same reasoning as
shared_expense_service's split functions: pure date math is exactly
the kind of logic that's cheap to get exhaustively right with tests
and expensive to get subtly wrong in production (an off-by-one here
means rent either double-charges or silently skips a month).
"""

from calendar import monthrange
from datetime import date, timedelta

Frequency = str  # "weekly" | "biweekly" | "monthly" | "quarterly" | "yearly"


def _parse_ymd(s: str) -> date:
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def _to_ymd(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _days_in_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def _add_months_anchored(anchor: str, months: int) -> str:
    """
    Add months while honoring the anchor day-of-month. If the anchor
    day doesn't exist in the target month (e.g. the 31st in
    February), clamp to that month's last day for THIS occurrence
    only — future occurrences still use the real anchor day, so rent
    due "on the 31st" stays anchored to the 31st (or each month's
    last day when the 31st doesn't exist), never permanently drifting
    to the 28th the way naive date-arithmetic libraries often do.
    """
    anchor_date = _parse_ymd(anchor)
    total = anchor_date.year * 12 + (anchor_date.month - 1) + months
    year = total // 12
    month = (total % 12) + 1
    day = min(anchor_date.day, _days_in_month(year, month))
    return _to_ymd(date(year, month, day))


def occurrence_at(start_date: str, frequency: Frequency, n: int) -> str:
    """The nth occurrence (0-based) of a schedule anchored at start_date."""
    if frequency == "weekly":
        return _to_ymd(_parse_ymd(start_date) + timedelta(days=7 * n))
    if frequency == "biweekly":
        return _to_ymd(_parse_ymd(start_date) + timedelta(days=14 * n))
    if frequency == "monthly":
        return _add_months_anchored(start_date, n)
    if frequency == "quarterly":
        return _add_months_anchored(start_date, 3 * n)
    if frequency == "yearly":
        return _add_months_anchored(start_date, 12 * n)
    raise ValueError(f"Unknown frequency: {frequency}")


def due_occurrences(
    *, start_date: str, frequency: Frequency, end_date: str | None, last_materialized: str | None, today: str, max_occurrences: int = 5000
) -> list[str]:
    """
    Every occurrence due on or before `today` that hasn't already been
    materialized (i.e. strictly after last_materialized), in
    chronological order. The 5000-occurrence safety cap mirrors the
    personal engine's own cap — a real guard against a corrupt or
    absurd rule (e.g. a weekly schedule anchored decades in the past)
    looping effectively forever, not a limit any real schedule should
    ever approach.
    """
    due: list[str] = []
    for n in range(max_occurrences + 1):
        occ = occurrence_at(start_date, frequency, n)
        if occ > today:
            break
        if end_date and occ > end_date:
            break
        if last_materialized and occ <= last_materialized:
            continue
        due.append(occ)
    return due
