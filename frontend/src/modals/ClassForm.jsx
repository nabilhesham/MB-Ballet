import { useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/** New + edit class. Pass `existing` to edit, omit it to create. */
export default function ClassForm({ existing, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [name, setName] = useState(existing?.name || '');
  const [description, setDescription] = useState(existing?.description || '');
  const [duration, setDuration] = useState(existing?.duration_hours ?? 1.5);
  const [level, setLevel] = useState(existing?.level || '');
  const [colour, setColour] = useState(existing?.colour || '#87438E');

  const save = async () => {
    if (!name.trim()) return toast('Name is required', 'bad');
    const body = { name, description, colour, duration_hours: Number(duration), level };
    try {
      if (existing) {
        await api(`/classes/${existing.id}`, { method: 'PUT', body });
        toast('Saved');
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
      <label>COLOUR</label>
      <input type="color" value={colour} onChange={e => setColour(e.target.value)} />
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>{existing ? 'Save' : 'Create'}</button>
      </div>
    </>
  );
}
