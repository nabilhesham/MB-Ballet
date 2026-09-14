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
