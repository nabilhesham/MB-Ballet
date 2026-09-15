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
  // A save that failed, kept on screen rather than only in a toast. The one
  // that matters is the duplicate-number refusal: it names the client the
  // number already belongs to and their member number, which reception has
  // to read and go and look up. A message that fades while they are still
  // reaching for it is no message at all.
  const [err, setErr] = useState('');

  const save = async () => {
    setErr('');
    if (!name.trim()) return toast('Name is required', 'bad');
    // Checked here as well as server-side, the same split every other rule
    // in this app uses: the form answers without a round trip, the endpoint
    // refuses independently. The sentences are kept identical to
    // access.phone_required()'s, so reception never sees the rule worded two
    // ways for one mistake.
    if (!phone.trim()) {
      return setErr('A mobile number is required — it is what identifies a client.');
    }
    if (phone.replace(/\D/g, '').length < 8) {
      return setErr('That does not look like a mobile number. It identifies the '
                    + 'client, so it has to be the real one.');
    }
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
    } catch (e) { setErr(e.message); toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>{existing ? 'Edit client' : 'New client'}</h3>
      {!existing && <div className="mh">Add their plan next — that is where sessions get assigned.</div>}
      {err && <div className="mh bad">{err}</div>}
      <div className="fieldrow">
        <div>
          <label>FULL NAME</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="Ahmed Hassan" />
        </div>
        <div>
          <label>MOBILE NUMBER</label>
          {/* Identity, not just a contact detail: two clients may share a
              name but never a number, and a client with none cannot be told
              apart from the next client with none. Required for that reason
              — including when editing, or it could be cleared a minute after
              being demanded. Unmarked, like FULL NAME above: this form has
              never carried required-field markers and adding one to half the
              required fields would read as the other half being optional. */}
          <input type="tel" inputMode="tel" value={phone}
                 onChange={e => { setPhone(e.target.value); setErr(''); }}
                 placeholder="01001234567" />
        </div>
      </div>
      <div className="fieldrow">
        <div>
          <label>AGE</label>
          {/* step="any": without it the browser defaults to whole numbers and
              refuses 3.5, which is exactly what the youngest classes need. */}
          <input type="number" step="any" min="1" max="99" value={age}
                 onChange={e => setAge(e.target.value)} placeholder="3.5" />
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
