import { useEffect, useState } from 'react';

import { api } from '../api';
import { isoDay, todayISO } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import SessionPickList from './SessionPickList';

/**
 * Sell a new plan, or renew one (pass `presetClassId` — the class dropdown
 * still shows, renewing only preselects it). A plan whose slots are not
 * assigned to real dates is a promise nobody has written down, so Save stays
 * disabled until every slot has a session.
 */
export default function PlanPicker({ clientId, presetClassId, classes, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [classId, setClassId] = useState(presetClassId || classes[0].id);
  const [name, setName] = useState('12 sessions');
  const [need, setNeed] = useState(12);
  const [start, setStart] = useState(todayISO());
  const [endsOn, setEndsOn] = useState('');
  // Reception can still type its own end date — a courtesy extension. Once
  // they have, the field stops following the picks rather than overwriting
  // what was just typed.
  const [endsTouched, setEndsTouched] = useState(false);
  const [price, setPrice] = useState('');
  const [sessions, setSessions] = useState([]);
  const [chosen, setChosen] = useState([]);

  const load = async cid => {
    const now = Math.floor(Date.now() / 1000);
    const list = await api(`/sessions?start=${now}&end=${now + 180 * 86400}&class_id=${cid}&available_for=${clientId}`);
    setSessions(list);
    setChosen([]);
  };

  // Sessions are always fetched for one class — the picker cannot show a
  // session the plan is not allowed to pay for, the same rule the server
  // enforces (this just never offers the mistake).
  useEffect(() => { load(classId); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // A plan is valid through the last session it pays for, so the end date is
  // derived from the picks rather than guessed at three months out. The
  // server applies the same rule when this is left blank; this only shows
  // reception the answer before they save.
  useEffect(() => {
    if (endsTouched) return;
    const last = sessions.reduce(
      (m, s) => (chosen.includes(s.id) && s.starts_at > m ? s.starts_at : m), 0);
    setEndsOn(last ? isoDay(last) : '');
  }, [chosen, sessions, endsTouched]);

  const onClassChange = e => {
    const id = Number(e.target.value);
    setClassId(id);
    load(id);
  };

  const onNeedChange = e => {
    const n = Number(e.target.value) || 0;
    setNeed(n);
    setChosen(c => (c.length > n ? c.slice(0, n) : c));
  };

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
      await api(`/clients/${clientId}/plan`, { method: 'POST', body: {
        class_id: classId, plan: name, sessions_total: Number(need),
        price: price === '' ? null : Number(price), starts_on: start, expires_on: endsOn,
        session_ids: chosen,
      } });
      close();
      toast('Plan saved — issue the card for this class next');
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  const k = classes.find(x => x.id === classId);
  const hint = k
    ? `Only ${k.name} sessions are offered — a plan pays for its own class.`
    : 'Pick a date for every session in the plan.';
  const canSave = chosen.length === need && need > 0;

  return (
    <>
      <h3>{presetClassId ? 'Renew plan' : 'New plan'}</h3>
      <div className="mh">
        A plan is bought for one class and pays only for that class's
        sessions. Renewing replaces the previous plan for the same class; plans for
        other classes are untouched.
      </div>

      <label>CLASS</label>
      <select value={classId} onChange={onClassChange}>
        {classes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>

      <div className="fieldrow">
        <div><label>PLAN NAME</label><input value={name} onChange={e => setName(e.target.value)} /></div>
        <div>
          <label>NUMBER OF SESSIONS</label>
          <input type="number" min="1" max="200" value={need} onChange={onNeedChange} />
        </div>
      </div>
      <div className="fieldrow">
        <div><label>STARTS ON</label><input type="date" value={start} onChange={e => setStart(e.target.value)} /></div>
        <div>
          <label>ENDS ON</label>
          <input type="date" value={endsOn}
                 onChange={e => { setEndsOn(e.target.value); setEndsTouched(true); }} />
          <div className="hint">Follows the sessions picked — type over it to override, until they change again.</div>
        </div>
      </div>
      <label>PRICE (EGP)</label>
      <input type="number" placeholder="optional" value={price} onChange={e => setPrice(e.target.value)} />

      <div className="divider" />
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <b style={{ fontSize: 14 }}>Assign the sessions</b>
        <span className={'pill ' + (chosen.length === need ? 'ok' : 'warn')}>{chosen.length} of {need} chosen</span>
      </div>
      <div className="sub" style={{ margin: '6px 0 10px' }}>{hint}</div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="sm" onClick={() => setChosen(sessions.slice(0, need).map(s => s.id))}>Auto-fill earliest</button>
        <button className="sm" onClick={() => setChosen([])}>Clear</button>
      </div>
      <SessionPickList sessions={sessions} chosen={chosen} onToggle={toggle} />

      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={!canSave} onClick={save}>Save plan</button>
      </div>
    </>
  );
}
