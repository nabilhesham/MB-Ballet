import { useEffect, useState } from 'react';

import { api } from '../api';
import { fetchPlanSessions } from '../lib/planSessions';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import SessionPickList from './SessionPickList';

/**
 * Book a client into an upcoming session of a class they already hold a plan
 * in — spends a slot the same way a scan at reception does. Only classes
 * with an unassigned slot are offered (the caller filters `classesEnrolled`
 * before opening this, same as app.js's addClientSession()).
 */
export default function AddSessionToPlan({ clientId, classesEnrolled, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [classId, setClassId] = useState(classesEnrolled[0].class_id);
  const need = classesEnrolled.find(k => k.class_id === classId)?.unassigned || 0;
  const [sessions, setSessions] = useState([]);
  const [chosen, setChosen] = useState([]);
  const [saving, setSaving] = useState(false);

  const load = async cid => {
    setSessions(await fetchPlanSessions(cid, clientId));
    setChosen([]);
  };

  useEffect(() => { load(classId); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const onClassChange = e => {
    const id = Number(e.target.value);
    setClassId(id);
    load(id);
  };

  const toggle = sid => setChosen(c => {
    if (c.includes(sid)) return c.filter(x => x !== sid);
    if (c.length >= need) {
      toast(`Only ${need} unassigned session${need === 1 ? '' : 's'} left on this plan — untick one first`, 'bad');
      return c;
    }
    return [...c, sid];
  });

  const save = async () => {
    if (!chosen.length) return;
    setSaving(true);
    const results = await Promise.allSettled(
      chosen.map(sid => api(`/sessions/${sid}/book`, { method: 'POST', body: { client_id: clientId } })),
    );
    const fail = results.filter(r => r.status === 'rejected');
    const ok = results.length - fail.length;
    if (!fail.length) {
      close(); toast(`Added ${ok} session${ok === 1 ? '' : 's'}`); onSaved();
    } else if (ok) {
      close(); toast(`Added ${ok}, ${fail.length} failed`, 'bad'); onSaved();
    } else {
      toast(fail[0].reason?.message || 'Could not add session', 'bad');
      setSaving(false);
    }
  };

  return (
    <>
      <h3>Add a session</h3>
      <div className="mh">
        Only a class with an unassigned slot on its plan is offered —
        booking one spends that slot, the same as a scan.
      </div>

      {classesEnrolled.length > 1 && (
        <>
          <label>CLASS</label>
          <select value={classId} onChange={onClassChange}>
            {classesEnrolled.map(k => (
              <option key={k.class_id} value={k.class_id}>{k.class_name} — {k.unassigned} unassigned</option>
            ))}
          </select>
        </>
      )}

      <div
        className="row"
        style={{
          justifyContent: 'space-between', alignItems: 'baseline',
          marginTop: classesEnrolled.length > 1 ? 14 : 0,
        }}
      >
        <b style={{ fontSize: 14 }}>Choose the session(s)</b>
        <span className={'pill ' + (chosen.length ? 'ok' : 'warn')}>{chosen.length} of {need} chosen</span>
      </div>
      <div className="sub" style={{ margin: '6px 0 10px' }}>
        {need} unassigned session{need === 1 ? '' : 's'} left on this plan.
      </div>

      <SessionPickList sessions={sessions} chosen={chosen} onToggle={toggle} />

      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={saving || chosen.length === 0} onClick={save}>Add</button>
      </div>
    </>
  );
}
