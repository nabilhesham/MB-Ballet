import { useState } from 'react';

import { api } from '../api';
import { todayISO } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/**
 * New + edit client. Pass `existing` to edit. `onCreated(id)` is called only
 * after a successful create — the caller navigates to the new profile, same
 * as app.js's saveClient() setting location.hash itself.
 */
export default function ClientForm({ existing, onSaved, onCreated }) {
  const { close } = useModal();
  const toast = useToast();

  const [name, setName] = useState(existing?.name_en || '');
  const [phone, setPhone] = useState(existing?.phone || '');
  const [age, setAge] = useState(existing?.age ?? '');
  const [joined, setJoined] = useState(existing?.joined_on || todayISO());
  const [school, setSchool] = useState(existing?.school || '');
  const [notes, setNotes] = useState(existing?.notes || '');

  const save = async () => {
    if (!name.trim()) return toast('Name is required', 'bad');
    const body = {
      name_en: name, phone, age: age === '' ? null : Number(age),
      school, joined_on: joined, notes,
    };
    try {
      if (existing) {
        await api(`/clients/${existing.id}`, { method: 'PUT', body });
        close();
        toast('Saved');
        onSaved();
      } else {
        const r = await api('/clients', { method: 'POST', body });
        close();
        toast('Client created');
        onCreated(r.id);
      }
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>{existing ? 'Edit client' : 'New client'}</h3>
      {!existing && <div className="mh">Add their plan next — that is where sessions get assigned.</div>}
      <div className="fieldrow">
        <div>
          <label>FULL NAME</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="Ahmed Hassan" />
        </div>
        <div>
          <label>MOBILE NUMBER</label>
          <input type="tel" inputMode="tel" value={phone} onChange={e => setPhone(e.target.value)}
                 placeholder="01001234567" />
        </div>
      </div>
      <div className="fieldrow">
        <div>
          <label>AGE</label>
          <input type="number" min="1" max="99" value={age} onChange={e => setAge(e.target.value)} />
        </div>
        <div>
          <label>FIRST JOINED</label>
          <input type="date" value={joined} onChange={e => setJoined(e.target.value)} />
        </div>
      </div>
      <label>SCHOOL / UNIVERSITY</label>
      <input value={school} onChange={e => setSchool(e.target.value)} placeholder="Manara Language School" />
      <label>NOTES</label>
      <textarea value={notes} onChange={e => setNotes(e.target.value)} />
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>{existing ? 'Save' : 'Create'}</button>
      </div>
    </>
  );
}
