import { useState } from 'react';

import { api } from '../api';
import { todayISO } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/**
 * Book an enquiry in: somebody who has rung up and is not a client yet.
 *
 * Deliberately lighter than ClientForm. None of the client identity rules
 * apply — the mobile is optional and may repeat, because the same family
 * rings about two children and the same person reschedules. See
 * api/appointments.py.
 */
export default function AppointmentForm({ onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [age, setAge] = useState('');
  const [onDate, setOnDate] = useState(todayISO());
  const [notes, setNotes] = useState('');
  const [err, setErr] = useState('');

  const save = async () => {
    setErr('');
    if (!name.trim()) return setErr('A name is required.');
    if (!onDate) return setErr('Pick the date of the appointment.');
    try {
      await api('/appointments', { method: 'POST', body: {
        name, phone, age: age === '' ? null : Number(age),
        on_date: onDate, notes,
      } });
      close();
      toast('Appointment booked');
      onSaved();
    } catch (e) { setErr(e.message); toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>New appointment</h3>
      <div className="mh">
        For somebody who is not a client yet. Enrolling them later is a separate
        step — this row stays as the record of the enquiry.
      </div>
      {err && <div className="mh bad">{err}</div>}

      <div className="fieldrow">
        <div>
          <label>FULL NAME</label>
          <input value={name} onChange={e => { setName(e.target.value); setErr(''); }}
                 placeholder="Ahmed Hassan" />
        </div>
        <div>
          <label>MOBILE NUMBER</label>
          <input type="tel" inputMode="tel" value={phone}
                 onChange={e => setPhone(e.target.value)} placeholder="01001234567" />
          <div className="hint">Optional here, unlike a client's.</div>
        </div>
      </div>
      <div className="fieldrow">
        <div>
          <label>AGE</label>
          {/* step="any" for the same reason ClientForm carries it: the
              youngest classes are placed on 3.5 and 4.8, and without it the
              browser refuses a decimal before it is ever sent. */}
          <input type="number" step="any" min="1" max="99" value={age}
                 onChange={e => setAge(e.target.value)} placeholder="3.5" />
        </div>
        <div>
          <label>APPOINTMENT DATE</label>
          <input type="date" value={onDate}
                 onChange={e => { setOnDate(e.target.value); setErr(''); }} />
        </div>
      </div>
      <label>NOTES (OPTIONAL)</label>
      <textarea value={notes} onChange={e => setNotes(e.target.value)}
                placeholder="Which class they asked about, who referred them" />

      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>Book it</button>
      </div>
    </>
  );
}
