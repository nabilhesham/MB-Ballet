import { useEffect, useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import SessionPickList from './SessionPickList';

/** Give a date to the paid-but-unassigned slots left on one plan. */
export default function AssignRemaining({ clientId, plan, onSaved }) {
  const { close } = useModal();
  const toast = useToast();
  const need = plan.unassigned;

  const [sessions, setSessions] = useState([]);
  const [chosen, setChosen] = useState([]);

  useEffect(() => {
    (async () => {
      const now = Math.floor(Date.now() / 1000);
      const list = await api(
        `/sessions?start=${now}&end=${now + 180 * 86400}&class_id=${plan.class_id}&available_for=${clientId}`,
      );
      setSessions(list);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = sid => setChosen(c => {
    if (c.includes(sid)) return c.filter(x => x !== sid);
    if (c.length >= need) {
      toast(`That is all ${need} sessions — untick one first`, 'bad');
      return c;
    }
    return [...c, sid];
  });

  const save = async () => {
    try {
      // eslint-disable-next-line no-restricted-syntax
      for (const sid of chosen) {
        // Sequential, matching app.js's saveTopUp() — one booking at a time.
        // eslint-disable-next-line no-await-in-loop
        await api(`/sessions/${sid}/book`, { method: 'POST', body: { client_id: clientId } });
      }
      close();
      toast('Sessions assigned');
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  const canSave = chosen.length === need && need > 0;

  return (
    <>
      <h3>Assign remaining sessions</h3>
      <div className="mh">
        {plan.plan} has {need} session{need === 1 ? '' : 's'} without a date. Only{' '}
        {plan.class_name || 'that class'}'s sessions are offered.
      </div>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline', marginTop: 14 }}>
        <b style={{ fontSize: 14 }}>Pick the dates</b>
        <span className={'pill ' + (chosen.length === need ? 'ok' : 'warn')}>{chosen.length} of {need} chosen</span>
      </div>
      <div className="row" style={{ margin: '10px 0 8px' }}>
        <button className="sm" onClick={() => setChosen(sessions.slice(0, need).map(s => s.id))}>Auto-fill earliest</button>
        <button className="sm" onClick={() => setChosen([])}>Clear</button>
      </div>
      <SessionPickList sessions={sessions} chosen={chosen} onToggle={toggle} />
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={!canSave} onClick={save}>Assign</button>
      </div>
    </>
  );
}
