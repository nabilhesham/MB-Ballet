"""/api/dashboard — the single landing-page summary endpoint, and /api/clock."""

import datetime
import time
from datetime import date

from fastapi import APIRouter, HTTPException

import access
import db
import repo as data


router = APIRouter()


@router.get("/api/clock")
def clock():
    """
    The server's own clock, for the browser to compare against its own.

    **This is the check CLAUDE.md's Known gaps asked for, and it needs no
    internet.** `db.now()` is `time.time()` on whatever machine runs the app,
    and that number decides when a class is over, when a no-show becomes
    absent, what "today" means for one-check-in-per-day, and what time a
    check-in is stamped with. A clock an hour fast is therefore an app that
    is wrong in four ways at once, and every one of them reads as a bug in
    the code rather than as a clock:

        a 3:30 class, 1.5 hours long, "completes after one hour" -- because
        the server thinks it is 17:00 when the wall clock says 16:00, so it
        has passed 17:00, the session's real end;

        and the same client's check-in, made at 15:40, shows 16:40 on the
        roster -- because the stamp is an absolute instant and the browser
        renders it honestly.

    Nothing in the app could tell a fast clock from a busy evening, and that
    is what this ends: the browser has a second clock, and two clocks that
    disagree are a fact rather than a guess. The frontend compares and says
    so; see `Shell.jsx`. Also returns the offset, because a *timezone* that
    disagrees breaks the day boundaries and the kiosk's "already checked in
    at HH:MM" line while leaving absolute timestamps looking right.

    Deliberately free: no database, no sweep. It is fetched by every screen.
    """
    t = db.now()
    off = datetime.datetime.now().astimezone().utcoffset()
    return {"epoch": t,
            "local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)),
            "offset_minutes": int(off.total_seconds() // 60) if off else 0}


@router.get("/api/dashboard/till")
def till(on: str = None):
    """
    What is behind TAKEN TODAY: every plan paid on that day and every
    product sold on it, named.

    **A drill-down rather than part of the dashboard payload.** The card is
    two numbers; the list behind it is two queries nobody needs until they
    ask, and the landing page is the one reception opens most — the same
    reasoning that moved the attention list off it (see CLAUDE.md). It is
    fetched when the popup opens.

    `on` defaults to today and is otherwise any ISO day, so the same popup
    answers "what did we take last Thursday" without a second endpoint.
    """
    day = on or date.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Pick a day.")
    repo = data.connect()
    try:
        tomorrow = access.next_day(day)
        plans = repo.plans_paid_between(day, tomorrow)
        products = repo.purchases_between(day, tomorrow)
        # Summed here from the rows already in hand rather than asked for
        # again: day_income() answers the same two figures for the card, and
        # a second aggregate would be a round trip spent on arithmetic.
        plan_paid = sum(p["price"] or 0 for p in plans)
        shop_paid = sum((s["qty"] or 0) * (s["unit_price"] or 0)
                        for s in products)
        return {"on": day, "plans": plans, "products": products,
                "plan_paid": round(plan_paid, 2),
                "shop_paid": round(shop_paid, 2),
                "paid": round(plan_paid + shop_paid, 2),
                "plan_unpriced": sum(1 for p in plans if p["price"] is None),
                "shop_unpriced": sum(1 for s in products
                                     if s["unit_price"] is None),
                "items": sum(s["qty"] or 0 for s in products)}
    finally:
        repo.close()


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
            # The till is plans *plus* the shop now, so the card shows the
            # total and the two halves under it. Both come out of one
            # day_income() call -- see access.day_income().
            "day_income": income["paid"],
            "day_plans": income["plans"],
            "day_unpriced": income["unpriced"],
            "day_plan_paid": income["plan_paid"],
            "day_shop_paid": income["shop_paid"],
            "day_shop_sales": income["shop_sales"],
            "day_shop_items": income["shop_items"],
            "day_shop_unpriced": income["shop_unpriced"],
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
