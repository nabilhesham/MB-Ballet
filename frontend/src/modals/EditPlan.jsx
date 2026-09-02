import { useEffect, useState } from 'react';

import { api } from '../api';
import { isoDay } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import Empty from '../components/Empty';
import SessionPickList from './SessionPickList';

/**
 * Edit a plan already sold: its name, its number of sessions, and its end
 * date. The class is shown but not editable — changing it would orphan the
 * plan's bookings and its card, and renewing is how a client moves class.
 *
 * Changing the count re-opens the same session picker PlanPicker uses, so
 * the plan can never be left with a count that doesn't match its bookings.
 * A session already marked present or absent is attendance history and is
 * shown locked, not offered for un-ticking.
 */
export default function EditPlan({ clientId, plan, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [loaded, setLoaded] = useState(false);
  const [name, setName] = useState(plan.plan);
  const [need, setNeed] = useState(plan.sessions_total);
  const [endsOn, setEndsOn] = useState(plan.expires_on);
  // Same rule as PlanPicker: reception can still type its own end date, but
  // only until the sessions on the plan change again — then it follows the
  // picks once more.
  const [endsTouched, setEndsTouched] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [chosen, setChosen] = useState([]);
  const [locked, setLocked] = useState([]);

  useEffect(() => {
    (async () => {
      const now = Math.floor(Date.now() / 1000);
      const [existing, available] = await Promise.all([
        api(`/clients/${clientId}/plan/${plan.id}/sessions`),
        api(`/sessions?start=${now}&end=${now + 180 * 86400}&class_id=${plan.class_id}&available_for=${clientId}`),
      ]);
      const existingNorm = existing.map(r => ({ ...r, id: r.session_id }));
      setSessions([...existingNorm, ...available].sort((a, b) => a.starts_at - b.starts_at));
      setChosen(existingNorm.map(r => r.id));
      setLocked(existingNorm.filter(r => r.status !== 'booked').map(r => r.id));
      setLoaded(true);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!loaded || endsTouched) return;
    const last = sessions.reduce(
      (m, s) => (chosen.includes(s.id) && s.starts_at > m ? s.starts_at : m), 0);
    setEndsOn(last ? isoDay(last) : plan.expires_on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chosen, sessions, endsTouched, loaded]);

  const onNeedChange = e => {
    const n = Number(e.target.value) || 0;
    if (n < locked.length) {
      return toast(`${locked.length} session${locked.length === 1 ? '' : 's'} on this plan `
        + 'are already attended — the plan cannot go below that', 'bad');
    }
    setNeed(n);
    // Deliberately does not touch `chosen` — which session to drop (or add)
    // is reception's call, not the app's. A mismatch just shows as a warning
    // below and keeps Save off until the ticks are adjusted by hand.
  };

  const toggle = sid => {
    if (locked.includes(sid)) return;
    setChosen(c => {
      if (c.includes(sid)) return c.filter(x => x !== sid);
      if (c.length >= need) {
        toast(`That is all ${need} sessions — untick one first`, 'bad');
        return c;
      }
      return [...c, sid];
    });
  };

  const save = async () => {
    if (!name.trim()) return toast('Name is required', 'bad');
    try {
      const r = await api(`/plans/${plan.id}`, { method: 'PUT', body: {
        plan: name, sessions_total: Number(need), expires_on: endsOn, session_ids: chosen,
      } });
      if (!r.ok) return toast(r.error, 'bad');
      close();
      toast('Plan updated — reissue the card to print the new numbers');
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  if (!loaded) return <Empty>Loading…</Empty>;

  const canSave = chosen.length === need && need > 0;

  return (
    <>
      <h3>Edit plan</h3>
      <div className="mh">
        {plan.class_name} — the class a plan is bought for cannot be changed here.
        Renew instead to move a client to a different class.
      </div>

      <div className="fieldrow">
        <div><label>PLAN NAME</label><input value={name} onChange={e => setName(e.target.value)} /></div>
        <div>
          <label>NUMBER OF SESSIONS</label>
          <input type="number" min={locked.length || 1} max="200" value={need} onChange={onNeedChange} />
        </div>
      </div>
      <label>ENDS ON</label>
      <input type="date" value={endsOn}
             onChange={e => { setEndsOn(e.target.value); setEndsTouched(true); }} />
      <div className="hint">Follows the sessions picked below — type over it to override, until they change again.</div>

      <div className="divider" />
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <b style={{ fontSize: 14 }}>Sessions on this plan</b>
        <span className={'pill ' + (chosen.length === need ? 'ok' : 'warn')}>{chosen.length} of {need} chosen</span>
      </div>
      <div className="sub" style={{ margin: '6px 0 10px' }}>
        Only {plan.class_name || 'this class'}'s sessions are offered. Sessions already attended
        are locked and always count toward the total.
      </div>
      {chosen.length !== need && (
        <div className="warnline" style={{ margin: '0 0 10px' }}>
          {chosen.length} of {need} sessions picked — Save stays off until they match.
          Tick or untick a session below, or change the number back.
        </div>
      )}
      <SessionPickList sessions={sessions} chosen={chosen} onToggle={toggle} locked={locked} />

      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={!canSave} onClick={save}>Save</button>
      </div>
    </>
  );
}
