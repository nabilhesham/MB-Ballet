"""/api/dashboard — the single landing-page summary endpoint."""

from datetime import date

from fastapi import APIRouter

import access
import db
import repo as data


router = APIRouter()


@router.get("/api/dashboard")
def dashboard(month_from: str = None, month_to: str = None):
    """
    Everything the landing page shows. The month range applies to the two
    intake figures alone — new clients and what they paid — because they are
    the only ones on the page that are about a period rather than about
    today. Left out, both default to the month we are in.
    """
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        midnight, tomorrow = access.day_bounds()

        today_sessions = repo.sessions_in_range(midnight, tomorrow)

        recent = repo.recent_events(midnight, 60)

        today = date.today().isoformat()
        exp = access.expected_today(repo)
        intake = access.month_intake(repo, month_from, month_to)
        # Today's till, by the day the money is dated rather than the day the
        # plan was typed in. See access.day_income().
        income = access.day_income(repo, today)
        # Both figures come out of one pass over today's events. They were
        # two counts over the identical window, which is a round trip spent
        # on arithmetic — on the page that already makes the most of them.
        events = repo.event_totals(midnight)
        stats = {
            **{f"exp_{k}": v for k, v in exp.items()},
            **{f"mo_{k}": v for k, v in intake.items()},
            "day_income": income["paid"],
            "day_plans": income["plans"],
            "day_unpriced": income["unpriced"],
            "scans_today": events["scans"],
            "denied_today": events["denied"],
            "active_clients": repo.count("clients", {"active": 1}),
            "classes": repo.count("classes", {"active": 1}),
            "sessions_week": repo.count("sessions", {
                "starts_at": {"gte": db.now(), "lte": db.now() + 7 * 86400},
                "status": "scheduled"}),
        }

        # No attention list here. It moved to Cards & renewals, which is the
        # screen that acts on it -- a renewal is done from a client's
        # profile, so a list of names on the landing page was a list you
        # navigated away from. access.day_attention() has one caller now,
        # /api/clients?status=attention&on=, and the dashboard is back to
        # being about what is happening today: who arrived, what is running,
        # and what came in.
        #
        # This also takes three round trips off the page -- the plans, their
        # states and their slot dates -- which is the whole of what the
        # day-scoped list cost here. See tests/test_query_budget.py.
        return {"stats": stats, "today_sessions": today_sessions,
                "recent": recent, "today": today}
    finally:
        repo.close()
