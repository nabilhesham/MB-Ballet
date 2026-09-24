import { useEffect, useRef, useState } from 'react';

import { api } from '../api';
import { isoDay, todayISO } from '../lib/format';
import { earliestUpcoming, fetchPlanSessions, useAutoPick, withinEndDate }
  from '../lib/planSessions';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import ClassPick from '../components/ClassPick';
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
  // Blank is a real answer, not a missing one: the plan is unpaid until a
  // date is put here, and reads that way everywhere it is shown.
  const [paidOn, setPaidOn] = useState('');
  const [notes, setNotes] = useState('');
  const [sessions, setSessions] = useState([]);
  const [chosen, setChosen] = useState([]);
  // How many of the auto-picked dates have already been and gone, and how
  // many fewer than asked for exist at all. Both are said out loud rather
  // than silently accepted -- see the lines under the list.
  const [auto, setAuto] = useState(null);

  const load = async cid => {
    setSessions(await fetchPlanSessions(cid, clientId));
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

  // Sessions past a *typed* end date are not offered: "ends on the 20th" and
  // "pays for a session on the 25th" cannot both be true. Deliberately not
  // applied to the auto-filled date — that one follows the picks, so capping
  // on it would let one pick hide every later session and make the last pick
  // impossible to move outwards again.
  const offered = endsTouched ? withinEndDate(sessions, endsOn) : sessions;

  // Anything the cap just excluded is unticked rather than left counted-but-
  // invisible: "12 of 12 chosen" beside a list that cannot show 12 is the
  // kind of disagreement nobody can debug from the screen.
  useEffect(() => {
    if (!endsTouched) return;
    const ok = new Set(offered.map(s => s.id));
    setChosen(c => (c.every(id => ok.has(id)) ? c : c.filter(id => ok.has(id))));
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [endsOn, endsTouched, sessions]);

  // Stating when the plan starts and how many sessions it buys is stating
  // which sessions those are, so the ticks and the end date follow both
  // fields instead of waiting to be filled in a second time. Everything
  // stays editable afterwards; this only saves the common case being done
  // by hand. The rule is server-side -- see useAutoPick().
  // The list on screen is a window (three weeks back, six months on) while
  // the rule that answers is not, so what comes back is narrowed to dates
  // this form can actually show. A ticked session with no row to tick is the
  // "12 of 12 chosen" disagreement nobody can debug from the screen.
  const shown = useRef([]);
  useEffect(() => { shown.current = sessions; }, [sessions]);

  useAutoPick({ classId, clientId, startsOn: start, total: need }, r => {
    const have = new Set(shown.current.map(x => x.id));
    const ids = r.session_ids.filter(id => have.has(id));
    setChosen(ids);
    const last = shown.current.reduce(
      (m, x) => (ids.includes(x.id) && x.starts_at > m ? x.starts_at : m), 0);
    setEndsOn(last ? isoDay(last) : '');
    // Back to following the picks. The cap applies to a date reception
    // typed, and this answer supersedes it: keeping it would hide the very
    // sessions just chosen for them.
    setEndsTouched(false);
    setAuto({ ...r, filled: ids.length });
  });

  const onClassChange = id => {
    setClassId(id);
    load(id);
    setAuto(null);
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
        paid_on: paidOn || null, notes: notes.trim() || null, session_ids: chosen,
      } });
      // Renewing replaces the plan the old card was issued against, and the
      // card prints the session count and end date of the plan it was made
      // for — so the old one is out of date the moment this saves. Issue the
      // replacement here rather than leaving reception to remember; the
      // endpoint revokes the previous card for this class as it goes.
      if (presetClassId) {
        try {
          await api(`/clients/${clientId}/card`, {
            method: 'POST', body: { class_id: classId },
          });
          toast('Plan renewed — new card issued, print it for the client');
        } catch (e) {
          toast(`Plan renewed, but the card could not be issued: ${e.message}`, 'bad');
        }
      } else {
        toast('Plan saved — issue the card for this class next');
      }
      close();
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  const k = classes.find(x => x.id === classId);
  const hint = k
    ? `Only ${k.name} sessions are offered — a plan pays for its own class. `
      + 'The last three weeks are included, for a plan written down after the client started.'
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
      <ClassPick classes={classes} value={classId} onChange={onClassChange} />

      <div className="fieldrow">
        <div><label>PLAN NAME</label><input value={name} onChange={e => setName(e.target.value)} /></div>
        <div>
          <label>NUMBER OF SESSIONS</label>
          <input type="number" min="1" max="200" value={need} onChange={onNeedChange} />
        </div>
      </div>
      <div className="fieldrow">
        <div>
          <label>STARTS ON</label>
          <input type="date" value={start} onChange={e => setStart(e.target.value)} />
          <div className="hint">Picks the sessions below, from this day on.</div>
        </div>
        <div>
          <label>ENDS ON</label>
          <input type="date" value={endsOn}
                 onChange={e => { setEndsOn(e.target.value); setEndsTouched(true); }} />
          <div className="hint">
            {endsTouched
              ? 'Only sessions on or before this date are offered below.'
              : 'Follows the sessions picked — type over it to override, until they change again.'}
          </div>
        </div>
      </div>
      <div className="fieldrow">
        <div>
          <label>PRICE (EGP)</label>
          <input type="number" placeholder="optional" value={price} onChange={e => setPrice(e.target.value)} />
        </div>
        <div>
          <label>PAID ON</label>
          <input type="date" value={paidOn} onChange={e => setPaidOn(e.target.value)} />
          <div className="hint">Leave blank if they have not paid yet — the plan shows as unpaid.</div>
        </div>
      </div>
      <label>NOTES (OPTIONAL)</label>
      <textarea value={notes} onChange={e => setNotes(e.target.value)}
                placeholder="About this plan — the client's own notes live on their profile" />

      <div className="divider" />
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <b style={{ fontSize: 14 }}>Assign the sessions</b>
        <span className={'pill ' + (chosen.length === need ? 'ok' : 'warn')}>{chosen.length} of {need} chosen</span>
      </div>
      <div className="sub" style={{ margin: '6px 0 10px' }}>{hint}</div>
      {auto && (auto.filled < need || auto.past > 0) && (
        <div className="warnline" style={{ margin: '0 0 10px' }}>
          {auto.filled < need && (
            <>Only {auto.filled} of {need} sessions could be filled from {start} —
              schedule more, or sell a shorter plan. </>
          )}
          {auto.past > 0 && (
            <>{auto.past} of the dates picked have already been and gone, so
              {auto.past === 1 ? ' it is' : ' they are'} recorded as absent when this
              saves — untick {auto.past === 1 ? 'it' : 'them'} if that is not right.</>
          )}
        </div>
      )}
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="sm" onClick={() => setChosen(earliestUpcoming(offered, need))}>Auto-fill earliest</button>
        <button className="sm" onClick={() => setChosen([])}>Clear</button>
      </div>
      <SessionPickList sessions={offered} chosen={chosen} onToggle={toggle} />

      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={!canSave} onClick={save}>Save plan</button>
      </div>
    </>
  );
}
