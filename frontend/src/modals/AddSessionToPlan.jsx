import { useEffect, useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import Empty from '../components/Empty';
import SessionPickTable from './SessionPickTable';

/**
 * Give one of a plan's unused slots a date.
 *
 * Two separate choices, and keeping them separate is the point: the plan
 * decides *who pays*, and it must be a plan with a slot still free; the
 * session decides *when they come*, and that may be any class. So a client
 * with a spare Ballet slot can be put into a Flexibility session without
 * buying a second plan, and their Ballet card still checks them in.
 *
 * Selling a plan is not like this — PlanPicker only ever offers its own
 * class's sessions. This is a correction, not a sale.
 */
export default function AddSessionToPlan({ clientId, classesEnrolled, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [planId, setPlanId] = useState(classesEnrolled[0].plan_id);
  const plan = classesEnrolled.find(k => k.plan_id === planId);
  const need = plan?.unassigned || 0;
  const [sessions, setSessions] = useState(null);
  const [chosen, setChosen] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      const now = Math.floor(Date.now() / 1000);
      const list = await api(`/sessions?start=${now}&end=${now + 180 * 86400}&available_for=${clientId}`);
      setSessions(list.filter(s => s.status !== 'cancelled'));
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!chosen) return toast('Pick a session', 'bad');
    setSaving(true);
    try {
      const r = await api(`/sessions/${chosen}/book`, {
        method: 'POST',
        body: { client_id: clientId, subscription_id: planId, allow_other_class: true },
      });
      if (!r.ok) { setSaving(false); return toast(r.error, 'bad'); }
      close();
      toast('Session added');
      return onSaved();
    } catch (e) { setSaving(false); return toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>Add a session</h3>
      <div className="mh">
        The plan pays for the slot; the session can be any class. Their card still
        checks them in, because it proves the plan rather than the class on the day.
      </div>

      <label>PAID FROM</label>
      <select value={planId} onChange={e => setPlanId(Number(e.target.value))}>
        {classesEnrolled.map(k => (
          <option key={k.plan_id} value={k.plan_id}>
            {k.class_name} — {k.unassigned} slot{k.unassigned === 1 ? '' : 's'} free
          </option>
        ))}
      </select>
      <div className="hint" style={{ marginBottom: 12 }}>
        {need} unassigned session{need === 1 ? '' : 's'} left on this plan.
      </div>

      {sessions === null
        ? <Empty>Loading…</Empty>
        : (
          <SessionPickTable
            sessions={sessions} chosenId={chosen} onPick={setChosen}
            emptyText="No sessions they are not already booked into."
          />
        )}

      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={!chosen || saving} onClick={save}>Add</button>
      </div>
    </>
  );
}
