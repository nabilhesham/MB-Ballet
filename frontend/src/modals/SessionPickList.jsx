import { fmtDay, fmtTime } from '../lib/format';

/**
 * The checkbox list of candidate sessions shared by PlanPicker, AssignRemaining,
 * AddSessionToPlan and EditPlan — same markup as app.js's renderPlanSessions()/
 * renderAddSessions(), which only differed in the buttons and disabled-logic
 * around them, not this list itself.
 *
 * `locked` (EditPlan only) disables a row instead of letting it be unticked —
 * a session already marked present or absent is attendance history, not a
 * pick. `booked` is only present on rows fetched from /sessions, not on a
 * plan's own already-assigned sessions, so it's rendered only when it exists.
 */
export default function SessionPickList({ sessions, chosen, onToggle, locked = [] }) {
  if (!sessions.length) {
    return <div className="empty">No upcoming sessions in this class. Schedule some first.</div>;
  }
  return (
    <div className="picklist">
      {sessions.map(s => {
        const isLocked = locked.includes(s.id);
        return (
          <label
            key={s.id}
            className={'pickrow' + (chosen.includes(s.id) ? ' on' : '') + (isLocked ? ' disabled' : '')}
          >
            <input
              type="checkbox" checked={chosen.includes(s.id)} disabled={isLocked}
              onChange={() => onToggle(s.id)}
            />
            <span className="dot" style={{ background: s.colour }} />
            <span className="pk-when">{fmtDay(s.starts_at)} · {fmtTime(s.starts_at)}</span>
            <span className="pk-class">{s.class_name}</span>
            <span className="pk-meta">
              {s.instructor_name || 'no instructor'}
              {s.booked != null ? ` · ${s.booked} booked` : ''}
              {isLocked ? ' · already attended' : ''}
            </span>
          </label>
        );
      })}
    </div>
  );
}
