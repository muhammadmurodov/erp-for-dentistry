"""What day it is, where the clinic is.

The server's `date.today()` is the server's day, which is the container's
day, which is UTC. The clinic is in Tashkent, five hours ahead. Between seven
in the evening and midnight those are different dates — and that is precisely
when a dental clinic is still seeing patients.

Everything that date would decide gets it wrong for those five hours: a
payment taken at nine in the evening is received *yesterday*, the dashboard
shows yesterday's takings as today's, the appointment booked for this evening
does not appear under today, and a cash session opened in the evening carries
the wrong business date — so the drawer is counted against a day that has
already been closed.

So business dates come from here. Timestamps do not: `created_at`,
`occurred_at` and every other instant stay UTC with a timezone, which is the
only sane way to store an instant. A **date** is not an instant; it is a day
in the place the work happened, and this is that place.

The zone is a constant rather than a setting because this system runs one
clinic. When it runs two in different zones, this becomes a column on
`organization`, and every caller already goes through one function.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

#: Asia/Tashkent: UTC+5, and no daylight saving since 1995.
CLINIC_TZ = ZoneInfo("Asia/Tashkent")


def clinic_now() -> datetime:
    """The current instant, as the clinic's wall clock reads it."""
    return datetime.now(CLINIC_TZ)


def clinic_today() -> date:
    """The clinic's date. Use this wherever `date.today()` was meant."""
    return clinic_now().date()


def clinic_time() -> time:
    """The clinic's time of day, to the minute."""
    return clinic_now().time().replace(second=0, microsecond=0)


def on_clinic_date(moment: datetime) -> date:
    """Which of the clinic's days an instant falls on.

    A UTC timestamp from the database is converted, not truncated: 2026-11-01
    at 20:30 in Tashkent is stored as 15:30 UTC on the same day, but 02:00 in
    Tashkent is stored as 21:00 UTC on the day before.
    """
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(CLINIC_TZ).date()
