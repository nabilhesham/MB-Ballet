import { useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/** New + edit instructor. Pass `existing` to edit, omit it to create. */
export default function InstructorForm({ existing, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [name, setName] = useState(existing?.name || '');
  const [phone, setPhone] = useState(existing?.phone || '');
  const [rate, setRate] = useState(existing?.hourly_rate || 0);
  const [specialty, setSpecialty] = useState(existing?.specialty || '');

  const save = async () => {
    if (!name.trim()) return toast('Name is required', 'bad');
    const body = { name, phone, specialty, hourly_rate: Number(rate || 0) };
    try {
      if (existing) {
        await api(`/instructors/${existing.id}`, { method: 'PUT', body });
        toast('Saved');
      } else {
        await api('/instructors', { method: 'POST', body });
        toast('Instructor added');
      }
      close();
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>{existing ? 'Edit instructor' : 'New instructor'}</h3>
      <label>NAME</label>
      <input value={name} onChange={e => setName(e.target.value)} />
      <div className="fieldrow">
        <div>
          <label>MOBILE</label>
          <input type="tel" value={phone} onChange={e => setPhone(e.target.value)} />
        </div>
        <div>
          <label>RATE (EGP PER HOUR)</label>
          <input type="number" min="0" step="10" value={rate} onChange={e => setRate(e.target.value)} />
        </div>
      </div>
      <label>SPECIALTY</label>
      <input value={specialty} onChange={e => setSpecialty(e.target.value)} placeholder="Classical ballet" />
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>{existing ? 'Save' : 'Create'}</button>
      </div>
    </>
  );
}
