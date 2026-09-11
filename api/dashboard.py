"""/api/dashboard — the single landing-page summary endpoint."""

from datetime import date, timedelta

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

        exp = access.expected_today(repo)
        intake = access.month_intake(repo, month_from, month_to)
        stats = {
            **{f"exp_{k}": v for k, v in exp.items()},
            **{f"mo_{k}": v for k, v in intake.items()},
            "scans_today": repo.count(
                "access_events", {"scanned_at": {"gte": midnight}, "source": "scan"}),
            "denied_today": repo.count(
                "access_events", {"scanned_at": {"gte": midnight}, "decision": "deny"}),
            "active_clients": repo.count("clients", {"active": 1}),
            "classes": repo.count("classes", {"active": 1}),
            "sessions_week": repo.count("sessions", {
                "starts_at": {"gte": db.now(), "lte": db.now() + 7 * 86400},
                "status": "scheduled"}),
        }

        today = date.today().isoformat()
        soon = (date.today() + timedelta(days=7)).isoformat()
        attention = []
        # One query for the plans, one for all their counts, rather than
        # plan_state() per client. On a local file the difference is
        # invisible; against a networked backend it is three round trips per
        # client, which is the whole page.
        live = repo.active_plans_with_clients()
        states = access.plan_states(repo, [r["sub_id"] for r in live])
        for r in live:
            st = states[r["sub_id"]]
            if st["frozen"]:
                continue          # deliberately paused, not a problem to chase
            # plan_state owns what a plan is valid through — the stored
            # column is only its floor.
            expires = st["expires_on"]
            if st["remaining"] <= 2 or expires <= soon or st["unassigned"] > 0:
                attention.append({
                    "id": r["id"], "name_en": r["name_en"], "phone": r["phone"],
                    "plan": r["plan"], "expires_on": expires,
                    "remaining": st["remaining"], "unassigned": st["unassigned"],
                    "expired": expires < today,
                })
        attention.sort(key=lambda x: (x["remaining"], x["expires_on"]))

        return {"stats": stats, "today_sessions": today_sessions,
                "recent": recent, "attention": attention[:20], "today": today}
    finally:
        repo.close()
