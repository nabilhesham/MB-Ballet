import { useEffect, useState } from 'react';

import { api } from '../api';
import { isoDay } from '../lib/format';
import { fetchPlanSessions } from '../lib/planSessions';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import ClassPick from '../components/ClassPick';
import Empty from '../components/Empty';
import SessionPickList from './SessionPickList';

/**
 * Edit a plan already sold: its name, its class, its number of sessions, and
 * its end date.
 *
 * Changing the count re-opens the same session picker PlanPicker uses, so
 * the plan can never be left with a count that doesn't match its bookings.
 * A session already marked present or absent is attendance history and is
 * shown locked, not offered for un-ticking.
 *
 * Changing the class is a correction of "this was written down against the
 * wrong one", so it takes the plan's slots with it: the old class's upcoming
 * dates are dropped and the new class's are picked here, in the same save.
 * Attendance is never moved — a session already present or absent stays on
 * the plan, locked, still on its own date in the class it happened in, so
 * only what is still ahead of the client changes.
 */
export default function EditPlan({ clientId, plan, classes = [], onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [loaded, setLoaded] = useState(false);
  const [name, setName] = useState(plan.plan);
  const [classId, setClassId] = useState(plan.class_id);
  const [need, setNeed] = useState(plan.sessions_total);
  const [endsOn, setEndsOn] = useState(plan.expires_on);
  // Same rule as PlanPicker: reception can still type its own end date, but
  // only until the sessions on the plan change again — then it follows the
  // picks once more.
  const [endsTouched, setEndsTouched] = useState(false);
  const [paidOn, setPaidOn] = useState(plan.paid_on || '');
  const [notes, setNotes] = useState(plan.notes || '');
  const [sessions, setSessions] = useState([]);
  const [chosen, setChosen] = useState([]);
  const [locked, setLocked] = useState([]);
  // The plan's own already-assigned sessions, kept aside for two jobs:
  // switching back to the class it started in restores them (a /sessions
  // call rightly does not offer dates the client is already booked into),
  // and the attended ones travel with the plan into whatever class it moves
  // to, since they are history and never move.
  const [own, setOwn] = useState([]);

  useEffect(() => {
    (async () => {
      const [existing, available] = await Promise.all([
        api(`/clients/${clientId}/plan/${plan.id}/sessions`),
        fetchPlanSessions(plan.class_id, clientId),
      ]);
      const existingNorm = existing.map(r => ({ ...r, id: r.session_id }));
      setOwn(existingNorm);
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

  const onClassChange = async id => {
    if (id === classId) return;
    setClassId(id);
    const available = await fetchPlanSessions(id, clientId);
    // Moving class re-picks only what is still ahead. The attended sessions
    // come along unchanged — they stay listed, stay locked and stay ticked,
    // which is also what the server requires: no edit may drop one.
    const back = id === plan.class_id;
    const keep = back ? own : own.filter(r => locked.includes(r.id));
    setSessions([...keep, ...available].sort((a, b) => a.starts_at - b.starts_at));
    setChosen(keep.map(r => r.id));
  };

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
      // Emptying the field means "unpaid again", and a null body field is
      // dropped before it reaches the server — hence the flag. See the
      // route's own note in api/plans.py.
      const r = await api(`/plans/${plan.id}${paidOn ? '' : '?clear_paid_on=true'}`, {
        method: 'PUT',
        body: {
          plan: name, class_id: classId, sessions_total: Number(need), expires_on: endsOn,
          paid_on: paidOn || null, notes, session_ids: chosen,
        },
      });
      if (!r.ok) return toast(r.error, 'bad');
      close();
      // The card prints the plan's end date and session count, and a printed
      // card is a snapshot nothing regenerates — so an edited plan leaves an
      // out-of-date card in the client's hand. Reissue it here rather than
      // leaving reception to remember; the endpoint revokes the previous card
      // for this class as it goes, and a class change needs the new class's
      // card anyway.
      try {
        await api(`/clients/${clientId}/card`, { method: 'POST', body: { class_id: classId } });
        toast('Plan updated — new card issued, print it for the client');
      } catch (e) {
        toast(`Plan updated, but the card could not be reissued: ${e.message}`, 'bad');
      }
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  if (!loaded) return <Empty>Loading…</Empty>;

  const canSave = chosen.length === need && need > 0;
  const movable = classes.length > 1;
  const moved = classId !== plan.class_id;
  const k = classes.find(x => x.id === classId);
  const className = k ? k.name : plan.class_name;

  return (
    <>
      <h3>Edit plan</h3>
      <div className="mh">
        A plan is bought for one class and pays only for that class's sessions.
        {movable
          ? ' Moving it to another class re-picks the dates still ahead and revokes'
            + ' the old class\'s card. Sessions already attended stay exactly as they'
            + ' are, on the day they happened.'
          : ' There is only one class to buy for.'}
      </div>

      <label>CLASS</label>
      {movable
        ? <ClassPick classes={classes} value={classId} onChange={onClassChange} />
        : (
          <div className="picklist">
            <div className="pickrow disabled">
              <span className="dot" style={{ background: plan.class_colour }} />
              <span className="pk-class">{plan.class_name}</span>
              <span className="pk-meta">the only class</span>
            </div>
          </div>
        )}
      {moved && locked.length > 0 && (
        <div className="hint" style={{ marginTop: 8 }}>
          {locked.length === 1
            ? `One attended session in ${plan.class_name} stays on this plan, on the day it happened.`
            : `${locked.length} attended sessions in ${plan.class_name} stay on this plan, `
              + 'on the days they happened.'}
          {' '}Moving the plan does not rewrite them — only the dates still ahead change.
        </div>
      )}

      <div className="fieldrow">
        <div><label>PLAN NAME</label><input value={name} onChange={e => setName(e.target.value)} /></div>
        <div>
          <label>NUMBER OF SESSIONS</label>
          <input type="number" min={locked.length || 1} max="200" value={need} onChange={onNeedChange} />
        </div>
      </div>
      <div className="fieldrow">
        <div>
          <label>ENDS ON</label>
          <input type="date" value={endsOn}
                 onChange={e => { setEndsOn(e.target.value); setEndsTouched(true); }} />
          <div className="hint">Follows the sessions picked below — type over it to override, until they change again.</div>
        </div>
        <div>
          <label>PAID ON</label>
          <input type="date" value={paidOn} onChange={e => setPaidOn(e.target.value)} />
          <div className="hint">Clear it to mark this plan unpaid again.</div>
        </div>
      </div>
      <label>NOTES (OPTIONAL)</label>
      <textarea value={notes} onChange={e => setNotes(e.target.value)}
                placeholder="About this plan — the client's own notes live on their profile" />

      <div className="divider" />
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <b style={{ fontSize: 14 }}>Sessions on this plan</b>
        <span className={'pill ' + (chosen.length === need ? 'ok' : 'warn')}>{chosen.length} of {need} chosen</span>
      </div>
      <div className="sub" style={{ margin: '6px 0 10px' }}>
        Only {className || 'this class'}'s sessions are offered, including the last three
        weeks. Sessions already attended are locked and always count toward the total
        {moved ? ', including the ones from the class this plan is moving out of' : ''}.
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
