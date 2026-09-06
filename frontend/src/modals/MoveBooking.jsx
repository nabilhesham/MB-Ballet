import { useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import SessionPickTable from './SessionPickTable';

/**
 * Move an upcoming booking to another session — of any class.
 *
 * The slot stays paid for by the plan that bought it; only the date it sits
 * on changes. That is what lets reception say "she could not make Ballet on
 * Tuesday, put her in Flexibility on Wednesday" without selling a second
 * plan, and her existing card still opens the door for it, because the scan
 * matches the plan's class rather than the session's.
 */
export default function MoveBooking({ clientId, fromSessionId, sessions, onSaved }) {
  const { close } = useModal();
  const toast = useToast();
  const [to, setTo] = useState(null);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!to) return toast('Pick a session to move to', 'bad');
    setSaving(true);
    try {
      const r = await api(`/clients/${clientId}/move-booking/${fromSessionId}`, {
        method: 'POST', body: { to_session_id: to, allow_other_class: true },
      });
      if (!r.ok) { setSaving(false); return toast(r.error, 'bad'); }
      close();
      toast('Session moved');
      return onSaved();
    } catch (e) { setSaving(false); return toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>Move this session</h3>
      <div className="mh">
        Any class is offered. The slot keeps the plan that paid for it, so their
        existing card still checks them in.
      </div>
      <SessionPickTable
        sessions={sessions} chosenId={to} onPick={setTo}
        emptyText="No other sessions to move to."
      />
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={!to || saving} onClick={save}>Move</button>
      </div>
    </>
  );
}
