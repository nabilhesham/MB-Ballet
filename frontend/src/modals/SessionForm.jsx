import { useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

function localInput(ts) {
  const d = ts ? new Date(ts * 1000) : new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

/**
 * Schedule a new session, or edit an existing one — same fields either way.
 * Pass `session` to edit; pass `presetTs`/`presetClassId` to prefill a new
 * one (e.g. from a tapped calendar slot, or a class's "Schedule session").
 */
export default function SessionForm({
  session, presetTs, presetClassId, classes, instructors, onSaved,
}) {
  const { close } = useModal();
  const toast = useToast();
  const editing = !!session;

  const initialClassId = session?.class_id ?? presetClassId ?? classes[0]?.id;
  const [classId, setClassId] = useState(initialClassId);
  const [instructorId, setInstructorId] = useState(() => {
    if (session?.instructor_id != null) return String(session.instructor_id);
    // A new session with no instructor named yet falls back to its class's
    // default — still a plain, changeable select, not a lock.
    if (!editing) {
      const k = classes.find(c => c.id === initialClassId);
      if (k?.instructor_id != null) return String(k.instructor_id);
    }
    return '';
  });
  const [when, setWhen] = useState(localInput(session?.starts_at ?? presetTs));
  const [duration, setDuration] = useState(
    session?.duration_hours ?? (classes.find(c => c.id === classId)?.duration_hours || 1.5),
  );
  const [notes, setNotes] = useState(session?.notes || '');

  const onClassChange = e => {
    const id = Number(e.target.value);
    setClassId(id);
    if (!editing) {
      const k = classes.find(c => c.id === id);
      if (k) setDuration(k.duration_hours);
      setInstructorId(k?.instructor_id != null ? String(k.instructor_id) : '');
    }
  };

  const save = async () => {
    if (!when) return toast('Pick a date and time', 'bad');
    const ins = instructorId === '' ? null : Number(instructorId);
    try {
      if (editing) {
        const body = {
          class_id: classId,
          starts_at: Math.floor(new Date(when).getTime() / 1000),
          duration_hours: Number(duration),
          notes,
        };
        // Moving a session into the future makes it scheduled again, so a
        // date corrected after the fact stops being reported as completed.
        if (new Date(when) > new Date()) body.status = 'scheduled';
        if (ins !== null) body.instructor_id = ins;
        // The server excludes null fields from a partial update, so clearing
        // the instructor back to none needs the dedicated query flag instead.
        const qs = ins === null ? '?clear_instructor=true' : '';
        await api(`/sessions/${session.id}${qs}`, { method: 'PUT', body });
        toast('Session updated');
      } else {
        await api('/sessions', { method: 'POST', body: {
          class_id: classId, instructor_id: ins,
          starts_at: Math.floor(new Date(when).getTime() / 1000),
          duration_hours: Number(duration), notes,
        } });
        toast('Session scheduled');
      }
      close();
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>{editing ? 'Edit session' : 'Schedule a session'}</h3>
      {editing && (
        <div className="mh">Moving a session does not notify anyone — tell the class yourself.</div>
      )}
      <label>CLASS</label>
      <select value={classId} onChange={onClassChange}>
        {classes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <label>INSTRUCTOR</label>
      <select value={instructorId} onChange={e => setInstructorId(e.target.value)}>
        <option value="">{editing ? '— none —' : '— none yet —'}</option>
        {instructors.map(i => (
          <option key={i.id} value={i.id}>{i.name}{i.specialty ? ` — ${i.specialty}` : ''}</option>
        ))}
      </select>
      <div className="fieldrow">
        <div>
          <label>DATE &amp; TIME</label>
          <input type="datetime-local" value={when} onChange={e => setWhen(e.target.value)} />
        </div>
        <div>
          <label>LENGTH (HOURS)</label>
          <input type="number" step="0.25" min="0.25" value={duration}
                 onChange={e => setDuration(e.target.value)} />
        </div>
      </div>
      <label>NOTES</label>
      <input value={notes} onChange={e => setNotes(e.target.value)} />
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>{editing ? 'Save changes' : 'Schedule'}</button>
      </div>
    </>
  );
}
