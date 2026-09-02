import { fmtDay, fmtTime } from '../lib/format';

/**
 * The checkbox list of candidate sessions shared by PlanPicker, AssignRemaining
 * and AddSessionToPlan — same markup as app.js's renderPlanSessions()/
 * renderAddSessions(), which only differed in the buttons and disabled-logic
 * around them, not this list itself.
 */
export default function SessionPickList({ sessions, chosen, onToggle }) {
  if (!sessions.length) {
    return <div className="empty">No upcoming sessions in this class. Schedule some first.</div>;
  }
  return (
    <div className="picklist">
      {sessions.map(s => (
        <label key={s.id} className={'pickrow' + (chosen.includes(s.id) ? ' on' : '')}>
          <input type="checkbox" checked={chosen.includes(s.id)} onChange={() => onToggle(s.id)} />
          <span className="dot" style={{ background: s.colour }} />
          <span className="pk-when">{fmtDay(s.starts_at)} · {fmtTime(s.starts_at)}</span>
          <span className="pk-class">{s.class_name}</span>
          <span className="pk-meta">{s.instructor_name || 'no instructor'} · {s.booked} booked</span>
        </label>
      ))}
    </div>
  );
}
