import { useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/*
 * A client goes away and asks to pause. Releasing their booked sessions is
 * the part that matters: left booked, every one would be swept to absent
 * while they are away and the plan would be empty on their return.
 */
export default function FreezePlan({ planId, clientName, className, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [mode, setMode] = useState('open');
  const [until, setUntil] = useState(() => {
    const d = new Date(); d.setMonth(d.getMonth() + 1);
    return d.toISOString().slice(0, 10);
  });
  const [reason, setReason] = useState('');

  const save = async () => {
    try {
      const r = await api(`/plans/${planId}/freeze`, {
        method: 'POST', body: { until: mode === 'dated' ? until : null, reason },
      });
      close();
      toast(r.released
        ? `Plan frozen — ${r.released} booked session${r.released === 1 ? '' : 's'} released`
        : 'Plan frozen');
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>Freeze {className ? `${className} plan` : 'this plan'}</h3>
      <div className="mh">
        {clientName} keeps every session they have paid for. The dates
        they are booked into during the pause are released, and the expiry moves out
        by however long the freeze lasts. Their other classes carry on as normal.
      </div>

      <label>HOW SHOULD IT END</label>
      <select value={mode} onChange={e => setMode(e.target.value)}>
        <option value="open">When I unfreeze it</option>
        <option value="dated">Automatically on a date</option>
      </select>

      {mode === 'dated' && (
        <div>
          <label>FROZEN UNTIL</label>
          <input type="date" value={until} onChange={e => setUntil(e.target.value)} />
          <div className="hint">It lifts itself on this date — nobody has to remember.</div>
        </div>
      )}

      <label>REASON</label>
      <input value={reason} onChange={e => setReason(e.target.value)} placeholder="Travelling, injury, exams…" />

      <div className="infoline" style={{ marginTop: 16 }}>
        While frozen, scanning their card is refused so a session cannot be spent
        by accident. Sessions already marked present or absent are untouched.
      </div>

      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>Freeze plan</button>
      </div>
    </>
  );
}
