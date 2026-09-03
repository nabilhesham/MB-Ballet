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

  const toggleDay = i => setDays(d => (d.includes(i) ? d.filter(x => x !== i) : [...d, i]));

  const onClassChange = e => {
    const id = Number(e.target.value);
    setClassId(id);
    const k = classes.find(c => c.id === id);
    setInstructorId(k?.instructor_id != null ? String(k.instructor_id) : '');
  };

  const save = async () => {
    const cls = classes.find(c => c.id === classId);
    try {
      const r = await api('/sessions/repeat', { method: 'POST', body: {
        class_id: classId,
        instructor_id: instructorId === '' ? null : Number(instructorId),
        starts_at: Math.floor(new Date(start).getTime() / 1000),
        weeks: Number(weeks), weekdays: days,
        duration_hours: cls ? cls.duration_hours : null,
      } });
      close();
      toast(`${r.created} sessions created`);
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
