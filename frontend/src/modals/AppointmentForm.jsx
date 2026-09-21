import { useState } from 'react';

import { api } from '../api';
import { todayISO } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/**
 * Book an enquiry in, or correct one: somebody who has rung up and is not a
 * client yet.
 *
 * One form for both, keyed on whether an `appt` was handed in. An enquiry is
 * a note taken over the phone, so every field on it can be wrong and the
 * edit has to offer all of them -- which makes it the same form, and two
 * copies of it would drift the moment a field was added to one.
 *
 * Deliberately lighter than ClientForm. None of the client identity rules
 * apply -- the mobile is optional and may repeat, because the same family
 * rings about two children and the same person reschedules. See
 * api/appointments.py.
 */
export default function AppointmentForm({ appt = null, onSaved }) {
  const { close } = useModal();
  const toast = useToast();
  const editing = !!appt;

  const [name, setName] = useState(appt?.name || '');
  const [phone, setPhone] = useState(appt?.phone || '');
  const [age, setAge] = useState(appt?.age ?? '');
  const [onDate, setOnDate] = useState(appt?.on_date || todayISO());
  const [onTime, setOnTime] = useState(appt?.on_time || '');
  const [notes, setNotes] = useState(appt?.notes || '');
  const [err, setErr] = useState('');

  const save = async () => {
    setErr('');
    if (!name.trim()) return setErr('A name is required.');
    if (!onDate) return setErr('Pick the date of the appointment.');
    try {
      await api(editing ? `/appointments/${appt.id}` : '/appointments', {
        method: editing ? 'PUT' : 'POST',
        body: {
          name, phone, age: age === '' ? null : Number(age),
          on_date: onDate, on_time: onTime || null, notes,
        },
      });
      close();
      toast(editing ? 'Appointment updated' : 'Appointment booked');
      onSaved();
    } catch (e) { setErr(e.message); toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>{editing ? 'Edit appointment' : 'New appointment'}</h3>
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
        <div>
          <label>TIME</label>
          <input type="time" value={onTime}
                 onChange={e => { setOnTime(e.target.value); setErr(''); }} />
          {/* Left blank on purpose when nobody said one. "Sometime Tuesday"
              is what a good half of these are, and a midnight stand-in
              would read on the list as an appointment at midnight. */}
          <div className="hint">Optional — leave it empty for "that day".</div>
        </div>
      </div>
      <label>NOTES (OPTIONAL)</label>
      <textarea value={notes} onChange={e => setNotes(e.target.value)}
                placeholder="Which class they asked about, who referred them" />

      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>{editing ? 'Save changes' : 'Book it'}</button>
      </div>
    </>
  );
}
