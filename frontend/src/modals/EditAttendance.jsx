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
 * Two separate corrections, kept as two buttons. "Mark present" fixes a tick
 * that was simply missed and touches nothing else. "Present at another
 * session" is for when they actually came on a different day, or to a
 * different class — it moves the booking there and marks it present in one
 * step.
 *
 * That second list is the last week plus everything still to come, not the
 * whole timetable: a correction is made within days of the session, and
 * offering two hundred rows to find one buries it.
 */
const BACK_DAYS = 7;
const FORWARD_DAYS = 180;
export default function EditAttendance({ clientId, sessionId, className, status, ts, onSaved }) {
  const { close } = useModal();
  const toast = useToast();
  const [picking, setPicking] = useState(false);
  const [sessions, setSessions] = useState(null);
  const [chosen, setChosen] = useState(null);
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
    const list = await api(`/sessions?start=${now - BACK_DAYS * 86400}`
      + `&end=${now + FORWARD_DAYS * 86400}&available_for=${clientId}`);
    setSessions(list.filter(s => s.status !== 'cancelled'));
  };

  const savePresent = async () => {
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
          The last week and everything upcoming, across every class. The slot moves
          to whichever you pick and is marked present — it keeps the plan that paid
          for it, so their card still works.
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
        <button className={status === 'absent' ? 'pri' : ''} onClick={() => fix('absent')}>
          Mark absent
        </button>
        <button className={status === 'present' ? 'pri' : ''} onClick={() => fix('present')}>
          Mark present
        </button>
      </div>
      <div className="row" style={{ justifyContent: 'flex-end', marginTop: 10 }}>
        <button className="sm" onClick={startPicking}>Present at another session…</button>
      </div>
    </>
  );
}
