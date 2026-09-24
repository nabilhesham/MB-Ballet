import { useEffect, useRef } from 'react';

import { api } from '../api';

/*
 * The candidate sessions that may be offered for one client, and the one
 * window every screen that offers them uses.
 *
 * Six places pick or repoint a session for a client — the four plan pickers
 * (PlanPicker, EditPlan, AddSessionToPlan, AssignRemaining), the attendance
 * correction (EditAttendance) and the move (MoveBooking). They each carried
 * their own idea of how far back to look: three weeks, one week, or no past
 * at all. The same receptionist doing the same job on the same client got a
 * different list depending on which button she pressed.
 */

/* Three weeks back. Reception writes a plan down after the client has
   already started coming, so the dates they actually attended have to be
   reachable; beyond three weeks it stops being a late entry and starts
   being a different term. */
const BACK_DAYS = 21;
const FORWARD_DAYS = 180;

const window_ = () => {
  const now = Math.floor(Date.now() / 1000);
  return `start=${now - BACK_DAYS * 86400}&end=${now + FORWARD_DAYS * 86400}`;
};

/* /api/sessions filters on no status at all, so a cancelled session comes
   back like any other. Booking a slot into one is meaningless, and looking
   backwards turns up three weeks more of them, so they are dropped here. */
const live = list => list.filter(s => s.status !== 'cancelled');

/** Has this session already finished? The same test the server books by. */
export const isPast = s => s.starts_at + s.duration_hours * 3600 < Date.now() / 1000;

/** One class's sessions — what a plan's own slots may be filled from. */
export async function fetchPlanSessions(classId, clientId) {
  return live(await api(
    `/sessions?${window_()}&class_id=${classId}&available_for=${clientId}`));
}

/**
 * Every class's sessions, for the corrections that are allowed to cross a
 * class: moving a booking, and "she was actually present at this one". The
 * slot keeps the plan that paid for it — see the correction rule in
 * CLAUDE.md — so the class is deliberately not narrowed here.
 */
export async function fetchAnyClassSessions(clientId) {
  return live(await api(`/sessions?${window_()}&available_for=${clientId}`));
}

/**
 * The earliest N sessions that have not happened yet — what "auto-fill"
 * means. It deliberately skips the past ones the window now includes:
 * assigning a slot to a finished session books it straight to absent, and
 * that is a choice to make one date at a time, never in bulk.
 */
export const earliestUpcoming = (sessions, n) =>
  sessions.filter(s => !isPast(s)).slice(0, n).map(s => s.id);

/**
 * The sessions still choosable once an end date has been typed in.
 *
 * "Ends on the 20th" and "pays for a session on the 25th" cannot both be
 * true, so once reception states an end date the list stops offering dates
 * past it. Inclusive of the day itself — a plan is valid *through* its last
 * session, so a session on the end date is exactly the normal case.
 *
 * `keep` is the ids that must stay visible whatever the date says: in
 * EditPlan, sessions already marked present or absent are attendance
 * history and can never be dropped from a plan, so hiding one would leave a
 * row counted in the total with nothing on screen to explain it.
 *
 * Only ever applied when the end date was *typed*, never when it is the
 * auto-filled one — see the note at its call sites. The auto-fill derives
 * the date from the picks, so capping on it would mean picking a session
 * could remove every later session from the list, and the last pick could
 * never be moved outwards again.
 */
export function withinEndDate(sessions, endsOn, keep = []) {
  if (!endsOn) return sessions;
  const limit = new Date(`${endsOn}T23:59:59`).getTime() / 1000;
  if (Number.isNaN(limit)) return sessions;
  return sessions.filter(s => s.starts_at <= limit || keep.includes(s.id));
}

/* A date input fires on every edit, so "2" on the way to "2026" arrives as a
   real value. The project's usual answer is to apply a date range on a
   button; a re-pick that is meant to feel automatic cannot do that, so it
   waits instead — long enough for a year to finish being typed, and only for
   a date that is actually complete. */
const SETTLE_MS = 400;
const looksLikeADay = d => /^\d{4}-\d{2}-\d{2}$/.test(d || '') && d >= '2000-01-01';

/**
 * Re-pick a plan's sessions whenever its start day or its session count
 * changes, and move the end date to the last of them.
 *
 * The rule itself is server-side (access.sessions_from_start, through
 * /plans/auto-sessions) because three forms need the same answer and one of
 * them — the reception kiosk — has no session list to work it out from. This
 * hook is only the plumbing: when to ask, and how not to let a stale answer
 * land on top of a newer one.
 *
 * `apply` is handed {session_ids, expires_on, past, short}. It is deliberately
 * not fired on the first render: opening a form must not silently rearrange a
 * plan somebody already agreed. Nothing here locks the form afterwards —
 * every tick, and the end date itself, can still be changed by hand before
 * saving, which is the point of re-picking rather than deciding.
 */
export function useAutoPick({ classId, clientId, startsOn, total, planId, ready = true }, apply) {
  const first = useRef(true);
  const seq = useRef(0);

  useEffect(() => {
    if (!ready) return undefined;
    if (first.current) { first.current = false; return undefined; }
    if (!classId || !clientId || !looksLikeADay(startsOn) || !(total > 0)) return undefined;

    const mine = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const r = await api('/plans/auto-sessions', { method: 'POST', body: {
          class_id: classId, client_id: clientId, starts_on: startsOn,
          sessions_total: Number(total), plan_id: planId ?? null,
        } });
        // A slower answer to an older question must never overwrite a newer
        // one — the same guard the kiosk's name search uses.
        if (mine === seq.current) apply(r);
      } catch { /* leave the form exactly as reception left it */ }
    }, SETTLE_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startsOn, total, ready]);
}
