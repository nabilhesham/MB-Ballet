import { useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

function localInput() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** Builds a term timetable in one go — used from Calendar, Sessions and ClassDetail. */
export default function RepeatSessions({ presetClassId, classes, instructors, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const initialClassId = presetClassId ?? classes[0]?.id;
  const [classId, setClassId] = useState(initialClassId);
  const [instructorId, setInstructorId] = useState(() => {
    const k = classes.find(c => c.id === initialClassId);
    return k?.instructor_id != null ? String(k.instructor_id) : '';
  });
  const [start, setStart] = useState(localInput());
  const [weeks, setWeeks] = useState(8);
  const [days, setDays] = useState([]);
  // How long every session in the term runs. It is a field rather than a
  // number taken quietly off the class, because this writes up to ninety-six
  // sessions and each one's length is its own from the moment it is created
  // — correcting the class afterwards changes none of them. The class's
  // usual length is the starting value and nothing more.
  const [duration, setDuration] = useState(
    () => classes.find(c => c.id === initialClassId)?.duration_hours ?? 1.5,
  );
  const [lengthTyped, setLengthTyped] = useState(false);

  const toggleDay = i => setDays(d => (d.includes(i) ? d.filter(x => x !== i) : [...d, i]));

  const onClassChange = e => {
    const id = Number(e.target.value);
    setClassId(id);
    const k = classes.find(c => c.id === id);
    setInstructorId(k?.instructor_id != null ? String(k.instructor_id) : '');
    if (k && !lengthTyped) setDuration(k.duration_hours);
  };

  const save = async () => {
    if (!(Number(duration) > 0)) {
      return toast('How long is the class? Give a length in hours.', 'bad');
    }
    try {
      const r = await api('/sessions/repeat', { method: 'POST', body: {
        class_id: classId,
        instructor_id: instructorId === '' ? null : Number(instructorId),
        starts_at: Math.floor(new Date(start).getTime() / 1000),
        weeks: Number(weeks), weekdays: days,
        duration_hours: Number(duration),
      } });
      close();
      // A term is generated in one go, so a clash is reported rather than
      // failing the batch — say how many and name the first, or reception
      // has no idea a date is quietly missing from the timetable.
      const n = r.skipped ? r.skipped.length : 0;
      if (n) {
        toast(`${r.created} created, ${n} skipped — ${r.skipped[0]}`
          + (n > 1 ? ` (and ${n - 1} more)` : ''), 'bad');
      } else {
        toast(`${r.created} sessions created`);
      }
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>Repeat weekly</h3>
      <div className="mh">Builds a term timetable in one go. Existing sessions at the same time are skipped.</div>
      <label>CLASS</label>
      <select value={classId} onChange={onClassChange}>
        {classes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <label>INSTRUCTOR</label>
      <select value={instructorId} onChange={e => setInstructorId(e.target.value)}>
        <option value="">— none yet —</option>
        {instructors.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}
      </select>
      <div className="fieldrow">
        <div>
          <label>FIRST SESSION</label>
          <input type="datetime-local" value={start} onChange={e => setStart(e.target.value)} />
        </div>
        <div>
          <label>FOR HOW MANY WEEKS</label>
          <input type="number" value={weeks} min="1" max="52" onChange={e => setWeeks(e.target.value)} />
        </div>
      </div>
      <label>LENGTH (HOURS)</label>
      <input type="number" step="0.25" min="0.25" value={duration}
             onChange={e => { setLengthTyped(true); setDuration(e.target.value); }} />
      <label>DAYS (LEAVE EMPTY FOR THE SAME WEEKDAY)</label>
      <div className="row">
        {DAYS.map((d, i) => (
          <label
            key={d}
            style={{
              display: 'flex', alignItems: 'center', gap: 5, margin: 0,
              letterSpacing: 0, fontSize: 12, color: 'var(--ink)', textTransform: 'none',
            }}
          >
            <input type="checkbox" style={{ width: 'auto' }} checked={days.includes(i)}
                   onChange={() => toggleDay(i)} /> {d}
          </label>
        ))}
      </div>
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>Create sessions</button>
      </div>
    </>
  );
}
