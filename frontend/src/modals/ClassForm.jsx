import { useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/** New + edit class. Pass `existing` to edit, omit it to create. */
export default function ClassForm({ existing, instructors = [], onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [name, setName] = useState(existing?.name || '');
  const [description, setDescription] = useState(existing?.description || '');
  const [duration, setDuration] = useState(existing?.duration_hours ?? 1.5);
  const [level, setLevel] = useState(existing?.level || '');
  const [colour, setColour] = useState(existing?.colour || '#87438E');
  const [instructorId, setInstructorId] = useState(
    existing?.instructor_id != null ? String(existing.instructor_id) : '',
  );

  const save = async () => {
    if (!name.trim()) return toast('Name is required', 'bad');
    const body = {
      name, description, colour, duration_hours: Number(duration), level,
      instructor_id: instructorId === '' ? null : Number(instructorId),
    };
    try {
      if (existing) {
        const r = await api(`/classes/${existing.id}`, { method: 'PUT', body });
        toast(r.cascaded_sessions
          ? `Saved — ${r.cascaded_sessions} upcoming session${r.cascaded_sessions === 1 ? '' : 's'} now show this instructor`
          : 'Saved');
      } else {
        await api('/classes', { method: 'POST', body });
        toast('Class created');
      }
      close();
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>{existing ? 'Edit class' : 'New class'}</h3>
      {!existing && <div className="mh">Sessions are scheduled separately once the class exists.</div>}
      <label>NAME</label>
      <input value={name} onChange={e => setName(e.target.value)} placeholder="Ballet" />
      <label>DESCRIPTION</label>
      <textarea value={description} onChange={e => setDescription(e.target.value)} />
      <div className="fieldrow">
        <div>
          <label>DEFAULT LENGTH (HOURS)</label>
          <input type="number" step="0.25" min="0.25" value={duration}
                 onChange={e => setDuration(e.target.value)} />
        </div>
        <div>
          <label>LEVEL</label>
          <input value={level} onChange={e => setLevel(e.target.value)} placeholder="All levels" />
        </div>
      </div>
      <label>INSTRUCTOR</label>
      <select value={instructorId} onChange={e => setInstructorId(e.target.value)}>
        <option value="">— none —</option>
        {instructors.map(i => (
          <option key={i.id} value={i.id}>{i.name}{i.specialty ? ` — ${i.specialty}` : ''}</option>
        ))}
      </select>
      <div className="hint">
        The default a new session for this class falls back to. Changing it also updates every
        upcoming session that hasn't happened yet — each one can still be changed individually.
      </div>
      <label>COLOUR</label>
      <input type="color" value={colour} onChange={e => setColour(e.target.value)} />
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>{existing ? 'Save' : 'Create'}</button>
      </div>
    </>
  );
}
