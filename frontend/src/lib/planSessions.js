import { api } from '../api';

/*
 * The candidate sessions a plan's paid slots may be filled from, shared by
 * PlanPicker, EditPlan, AddSessionToPlan and AssignRemaining — the four
 * places that do the same job, so the window lives here once instead of
 * being retyped four times.
 */

/* Three weeks back. Reception writes a plan down after the client has
   already started coming, so the dates they actually attended have to be
   reachable; beyond three weeks it stops being a late entry and starts
   being a different term. */
const BACK_DAYS = 21;
const FORWARD_DAYS = 180;

/** Has this session already finished? The same test the server books by. */
export const isPast = s => s.starts_at + s.duration_hours * 3600 < Date.now() / 1000;

export async function fetchPlanSessions(classId, clientId) {
  const now = Math.floor(Date.now() / 1000);
  const list = await api(`/sessions?start=${now - BACK_DAYS * 86400}`
    + `&end=${now + FORWARD_DAYS * 86400}&class_id=${classId}&available_for=${clientId}`);
  // /api/sessions filters on no status at all, so a cancelled session comes
  // back like any other. Booking a slot into one is meaningless, and looking
  // backwards turns up three weeks more of them, so they are dropped here.
  return list.filter(s => s.status !== 'cancelled');
}

/**
 * The earliest N sessions that have not happened yet — what "auto-fill"
 * means. It deliberately skips the past ones the window now includes:
 * assigning a slot to a finished session books it straight to absent, and
 * that is a choice to make one date at a time, never in bulk.
 */
export const earliestUpcoming = (sessions, n) =>
  sessions.filter(s => !isPast(s)).slice(0, n).map(s => s.id);
