import { useState } from 'react';

/**
 * Pick one class from a searchable list — the plan pickers' class field.
 *
 * It replaced a plain <select>: the classes are deliberately fine-grained
 * ("Ballet Level 8", "Ballet Grade 6", "Evening Flexibility" — one roster
 * block each), so a dropdown means reading the whole list to find one, and
 * this is the field every other choice in the modal hangs off. Searching
 * matches the instructor and level too, since "who teaches it" is often what
 * reception remembers.
 *
 * Same vocabulary as SessionPickList — .picklist/.pickrow — with a radio
 * rather than a checkbox, because a plan is bought for exactly one class.
 */
export default function ClassPick({ classes, value, onChange }) {
  const [q, setQ] = useState('');
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? classes.filter(k => [k.name, k.level, k.instructor_name]
        .some(v => (v || '').toLowerCase().includes(needle)))
    : classes;

  return (
    <>
      <div className="row" style={{ marginBottom: 8 }}>
        <input
          className="search" type="search" placeholder="Search by class, level or instructor…"
          value={q} onChange={e => setQ(e.target.value)}
        />
        <span className="dt-count">
          {needle ? `${shown.length} of ${classes.length}` : `${classes.length} classes`}
        </span>
      </div>
      {shown.length ? (
        <div className="picklist" style={{ maxHeight: '26vh' }}>
          {shown.map(k => (
            <label key={k.id} className={'pickrow' + (k.id === value ? ' on' : '')}>
              <input
                type="radio" name="classpick" checked={k.id === value}
                onChange={() => onChange(k.id)}
              />
              <span className="dot" style={{ background: k.colour }} />
              <span className="pk-class">{k.name}</span>
              <span className="pk-meta">
                {k.level || 'no level'} · {k.instructor_name || 'no instructor'}
              </span>
            </label>
          ))}
        </div>
      ) : (
        <div className="empty">No class matches that search.</div>
      )}
    </>
  );
}
