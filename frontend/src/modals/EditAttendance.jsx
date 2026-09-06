import { useState } from 'react';

import { api } from '../api';
import { fmtFull } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import { StatusPill } from '../components/Pill';
import Empty from '../components/Empty';
import SessionPickTable from './SessionPickTable';

/**
 * Correct a past attendance record from a client's history table.
 *
 * Marking someone present asks *which* session they were present at, across
 * every class — because the usual reason a row is wrong is that they came on
 * a different day, or to a different class, rather than that the tick was
 * simply missed. Their own session is first in the list and selected, so the
 * ordinary correction is still one click; picking any other row moves the
 * booking there and marks it present in the same step.
 */
export default function EditAttendance({ clientId, sessionId, className, status, ts, onSaved }) {
  const { close } = useModal();
  const toast = useToast();
  const [picking, setPicking] = useState(false);
  const [sessions, setSessions] = useState(null);
  const [chosen, setChosen] = useState(sessionId);
  const [saving, setSaving] = useState(false);

  const fix = async newStatus => {
    try {
      await api(`/sessions/${sessionId}/status-of`, { method: 'POST', body: { client_id: clientId, status: newStatus } });
      close();
      toast(newStatus === 'present' ? 'Marked present' : 'Marked absent');
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  const startPicking = async () => {
    setPicking(true);
    const now = Math.floor(Date.now() / 1000);
    // A wide window either side: the session they really attended is usually
    // near the one that was marked wrong, but not always.
    const list = await api(`/sessions?start=${now - 120 * 86400}&end=${now + 120 * 86400}`);
    setSessions(list.filter(s => s.status !== 'cancelled'));
  };

  const savePresent = async () => {
    if (chosen === sessionId) return fix('present');
    setSaving(true);
    try {
      const r = await api(`/clients/${clientId}/move-booking/${sessionId}`, {
        method: 'POST',
        body: { to_session_id: chosen, allow_other_class: true, status: 'present' },
      });
      if (!r.ok) { setSaving(false); return toast(r.error, 'bad'); }
      close();
      toast('Moved and marked present');
      return onSaved();
    } catch (e) { setSaving(false); return toast(e.message, 'bad'); }
  };

  if (picking) {
    return (
      <>
        <h3>Which session did they attend?</h3>
        <div className="mh">
          Their own session is first. Pick a different one — of any class — and the
          slot moves there and is marked present.
        </div>
        {sessions === null
          ? <Empty>Loading…</Empty>
          : (
            <SessionPickTable
              sessions={sessions} chosenId={chosen} onPick={setChosen}
              emptyText="No sessions to choose from."
            />
          )}
        <div className="acts">
          <button onClick={() => setPicking(false)}>Back</button>
          <button className="pri" disabled={!chosen || saving} onClick={savePresent}>
            Mark present
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <h3>{className}</h3>
      <div className="mh">{fmtFull(ts)}</div>
      <div className="row" style={{ margin: '18px 0' }}>Currently <StatusPill status={status} /></div>
      <div className="sub">
        Both present and absent use the client's slot. Changing this only
        corrects the record of what happened.
      </div>
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className={status === 'absent' ? 'pri' : ''} onClick={() => fix('absent')}>Mark absent</button>
        <button className={status === 'present' ? 'pri' : ''} onClick={startPicking}>Mark present</button>
      </div>
    </>
  );
}
