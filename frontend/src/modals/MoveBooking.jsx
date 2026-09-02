import { useState } from 'react';

import { api } from '../api';
import { fmtFull } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/**
 * Move an upcoming booking to another date of the same class. The caller
 * fetches the candidate session list and only opens this if it's non-empty
 * (matching app.js's moveBooking(), which toasts and never opens the modal
 * otherwise) — so this component always has at least one option.
 */
export default function MoveBooking({ clientId, fromSessionId, sessions, onSaved }) {
  const { close } = useModal();
  const toast = useToast();
  const [to, setTo] = useState(sessions[0].id);

  const save = async () => {
    try {
      await api(`/clients/${clientId}/move-booking/${fromSessionId}`, {
        method: 'POST', body: { to_session_id: to },
      });
      close();
      toast('Session moved');
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>Move this session</h3>
      <div className="mh">Only sessions of the same class are offered.</div>
      <label>MOVE TO</label>
      <select value={to} onChange={e => setTo(Number(e.target.value))}>
        {sessions.map(s => (
          <option key={s.id} value={s.id}>
            {fmtFull(s.starts_at)} — {s.instructor_name || 'no instructor'} ({s.booked} booked)
          </option>
        ))}
      </select>
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>Move</button>
      </div>
    </>
  );
}
