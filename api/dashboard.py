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

        # The attention list is about **today**, not about a standing
        # condition. access.day_attention() is the whole rule and the Cards
        # screen asks it the same question about whatever date reception
        # picks; see the note above it for why a week-wide "expiring soon"
        # stopped being read.
        #
        # One query for the plans and one for all their counts, rather than
        # plan_state() per client. On a local file the difference is
        # invisible; against a networked backend it is three round trips per
        # client, which is the whole page.
        live = repo.active_plans_with_clients()
        states = access.plan_states(repo, [r["sub_id"] for r in live])
        flags = access.day_attention(repo, today, states)
        attention = []
        for r in live:
            why = flags.get(r["sub_id"])
            if not why:
                continue
            st = states[r["sub_id"]]
            attention.append({
                "id": r["id"], "name_en": r["name_en"], "phone": r["phone"],
                "plan": r["plan"], "expires_on": st["expires_on"],
                "remaining": why["remaining_on"],
                "unassigned": st["unassigned"],
                "expired": st["expires_on"] < today,
                "renew": why["renew"], "ran_out": why["ran_out"],
                "one_left": why["one_left"],
            })
        # Emptiest first, then the earliest end date: the person who cannot
        # come back tomorrow is the one to speak to first.
        attention.sort(key=lambda x: (x["remaining"], x["expires_on"], x["id"]))

        return {"stats": stats, "today_sessions": today_sessions,
                "recent": recent, "attention": attention[:20], "today": today}
    finally:
        repo.close()
