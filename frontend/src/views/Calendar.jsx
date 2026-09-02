import { useEffect, useReducer, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api } from '../api';
import { fmtTime, hrs } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import Empty from '../components/Empty';
import SessionForm from '../modals/SessionForm';
import RepeatSessions from '../modals/RepeatSessions';

/*
 * The week runs Saturday to Friday, which is how the academy's timetable is
 * read locally. Everything below derives from WEEK_START rather than
 * assuming Monday, so changing it here is enough.
 *
 * calAnchor/calMode are module-level, not component state, so navigating
 * away and back remembers the last position — same as static/app.js's
 * module-level globals of the same name. bump() forces a re-render after
 * mutating them directly.
 */
const WEEK_START = 6; // 0=Sun 1=Mon ... 6=Sat
const ROW_PX = 46;
let _calAnchor = null;
let _calMode = 'auto'; // 'auto' | 'week' | 'month' | 'agenda'

function weekStartOf(d) {
  const x = new Date(d); x.setHours(0, 0, 0, 0);
  const diff = (x.getDay() - WEEK_START + 7) % 7;
  x.setDate(x.getDate() - diff);
  return x;
}

function assignLanes(list) {
  const out = []; const lanes = [];
  list.forEach(s => {
    const s1 = s.starts_at; const s2 = s.starts_at + s.duration_hours * 3600;
    let idx = lanes.findIndex(end => end <= s1);
    if (idx === -1) { lanes.push(s2); idx = lanes.length - 1; } else lanes[idx] = s2;
    out.push({ lane: idx });
  });
  const of = Math.max(1, lanes.length);
  return out.map(o => ({ ...o, of }));
}

function useNarrow(threshold) {
  const [narrow, setNarrow] = useState(() => window.innerWidth < threshold);
  useEffect(() => {
    let t;
    const onResize = () => {
      clearTimeout(t);
      t = setTimeout(() => setNarrow(window.innerWidth < threshold), 250);
    };
    window.addEventListener('resize', onResize);
    return () => { clearTimeout(t); window.removeEventListener('resize', onResize); };
  }, [threshold]);
  return narrow;
}

function WeekGrid({ days, sessions, hours, todayStr, lo, hi, onSlotClick, onEventClick }) {
  const wrapRef = useRef(null);
  const now = new Date();
  const nowIdx = days.findIndex(d => d.toDateString() === now.toDateString());
  const nowH = now.getHours() + now.getMinutes() / 60;
  const showNow = nowIdx >= 0 && nowH >= lo && nowH <= hi;

  // Scrolls the grid to the current time once, same as static/app.js's
  // placeNowLine() — the line itself is plain conditional JSX below instead
  // of an imperatively appended DOM node, since React already owns this tree.
  useEffect(() => {
    if (showNow && wrapRef.current) {
      wrapRef.current.scrollTop = Math.max(0, (nowH - lo - 2) * ROW_PX);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="calwrap" style={{ '--row': `${ROW_PX}px` }} ref={wrapRef}>
      <div className="calhead">
        <div className="corner" />
        {days.map((d, i) => (
          <div key={i} className={'hd' + (d.toDateString() === todayStr ? ' today' : '')}>
            <div className="d">{d.toLocaleDateString([], { weekday: 'short' }).toUpperCase()}</div>
            <div className="n">{d.getDate()}</div>
          </div>
        ))}
      </div>
      <div className="calbody">
        <div className="hourcol">
          {hours.map(h => <div key={h} className="hr">{String(h).padStart(2, '0')}:00</div>)}
        </div>
        {days.map((day, i) => {
          const dayStart = day.getTime() / 1000; const dayEnd = dayStart + 86400;
          const mine = sessions.filter(s => s.starts_at >= dayStart && s.starts_at < dayEnd);
          const lanes = assignLanes(mine);
          return (
            <div key={i} className={'daycol' + (day.toDateString() === todayStr ? ' today' : '')} data-day={i}>
              {hours.map(h => {
                const cell = new Date(day); cell.setHours(h, 0, 0, 0);
                return <div key={h} className="slot" onClick={() => onSlotClick(Math.floor(cell.getTime() / 1000))} />;
              })}
              {mine.map((s, idx) => {
                const d = new Date(s.starts_at * 1000);
                const top = ((d.getHours() - lo) + d.getMinutes() / 60) * ROW_PX;
                const evH = Math.max(20, s.duration_hours * ROW_PX - 2);
                const { lane, of } = lanes[idx];
                const w = 100 / of;
                return (
                  <div
                    key={s.id} className={'ev' + (s.status !== 'scheduled' ? ' cancelled' : '')}
                    style={{
                      top, height: evH, left: `calc(${lane * w}% + 2px)`, width: `calc(${w}% - 4px)`,
                      background: `${s.colour}22`, borderLeftColor: s.colour,
                    }}
                    onClick={e => { e.stopPropagation(); onEventClick(s.id); }}
                    title={`${s.class_name} · ${fmtTime(s.starts_at)}`}
                  >
                    <div className="t">{fmtTime(s.starts_at)}</div>
                    <div className="nm">{s.class_name}</div>
                    {evH > 52 && <div className="t">{s.attended}/{s.booked} in</div>}
                  </div>
                );
              })}
              {i === nowIdx && showNow && <div className="nowline" style={{ top: (nowH - lo) * ROW_PX }} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MonthGrid({ days, sessions, todayStr, month, onCellClick, onEventClick }) {
  const names = [...Array(7)].map((_, i) => {
    const d = new Date(2024, 0, 7 + ((WEEK_START + i) % 7));
    return d.toLocaleDateString([], { weekday: 'short' }).toUpperCase();
  });
  return (
    <div className="calwrap">
      <div className="monthhead">{names.map(n => <div key={n}>{n}</div>)}</div>
      <div className="monthgrid">
        {days.map((day, i) => {
          const dayStart = day.getTime() / 1000; const dayEnd = dayStart + 86400;
          const mine = sessions.filter(s => s.starts_at >= dayStart && s.starts_at < dayEnd)
            .sort((a, b) => a.starts_at - b.starts_at);
          const other = day.getMonth() !== month;
          return (
            <div
              key={i}
              className={'mcell' + (other ? ' other' : '') + (day.toDateString() === todayStr ? ' today' : '')}
              onClick={() => onCellClick(Math.floor(dayStart) + 16 * 3600)}
            >
              <div className="mnum">{day.getDate()}</div>
              {mine.slice(0, 4).map(s => (
                <div
                  key={s.id} className={'mev' + (s.status !== 'scheduled' ? ' cancelled' : '')}
                  style={{ background: `${s.colour}22`, borderLeft: `3px solid ${s.colour}` }}
                  onClick={e => { e.stopPropagation(); onEventClick(s.id); }}
                  title={`${s.class_name} ${fmtTime(s.starts_at)}`}
                >
                  <span className="t">{fmtTime(s.starts_at)}</span> {s.class_name}
                </div>
              ))}
              {mine.length > 4 && <div className="mmore">+{mine.length - 4} more</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AgendaList({ days, sessions, todayStr, onRowClick, onAddClick }) {
  return (
    <div className="agenda">
      {days.map((day, i) => {
        const dayStart = day.getTime() / 1000; const dayEnd = dayStart + 86400;
        const mine = sessions.filter(s => s.starts_at >= dayStart && s.starts_at < dayEnd)
          .sort((a, b) => a.starts_at - b.starts_at);
        if (!mine.length && days.length > 10) return null; // month list: skip empty days
        const isToday = day.toDateString() === todayStr;
        return (
          <div key={i} className="day">
            <div className={'dayhd' + (isToday ? ' today' : '')}>
              <span className="dn">{day.toLocaleDateString([], { weekday: 'long' })}</span>
              <span className="dd">
                {day.toLocaleDateString([], { day: 'numeric', month: 'short' })}{isToday ? ' · today' : ''}
              </span>
            </div>
            {mine.length ? mine.map(s => (
              <div key={s.id} className="slotrow" onClick={() => onRowClick(s.id)}>
                <div className="tm">{fmtTime(s.starts_at)}</div>
                <div className="bd">
                  <div className="nm"><span className="dot" style={{ background: s.colour }} />{s.class_name}</div>
                  <div className="sub">
                    {s.instructor_name || 'no instructor'} · {hrs(s.duration_hours)} · {s.attended}/{s.booked} in
                  </div>
                </div>
                {s.status !== 'scheduled' && <span className={'pill ' + (s.status === 'cancelled' ? 'bad' : 'grey')}>{s.status}</span>}
              </div>
            )) : (
              <div className="empty-day">
                Nothing scheduled.{' '}
                <a href="#" onClick={e => { e.preventDefault(); onAddClick(Math.floor(dayStart) + 16 * 3600); }}
                   style={{ color: 'var(--brand)' }}>Add a class</a>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function Calendar() {
  const [, bump] = useReducer(x => x + 1, 0);
  if (!_calAnchor) _calAnchor = new Date();
  const narrow = useNarrow(760);
  const { open } = useModal();
  const toast = useToast();
  const nav = useNavigate();

  const mode = _calMode === 'auto' ? (narrow ? 'agenda' : 'week') : _calMode;

  let from; let to;
  if (mode === 'month') {
    const first = new Date(_calAnchor.getFullYear(), _calAnchor.getMonth(), 1);
    const last = new Date(_calAnchor.getFullYear(), _calAnchor.getMonth() + 1, 0);
    from = weekStartOf(first);
    to = new Date(weekStartOf(last)); to.setDate(to.getDate() + 7);
  } else {
    from = weekStartOf(_calAnchor);
    to = new Date(from); to.setDate(to.getDate() + 7);
  }
  const start = Math.floor(from.getTime() / 1000);
  const end = Math.floor(to.getTime() / 1000);

  const [sessions, setSessions] = useState(null);
  const [classes, setClasses] = useState(null);
  const [reloadTick, setReloadTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api(`/sessions?start=${start}&end=${end}`), api('/classes')]).then(([s, cl]) => {
      if (!cancelled) { setSessions(s); setClasses(cl); }
    });
    return () => { cancelled = true; };
  }, [start, end, reloadTick]);
  const reload = () => setReloadTick(t => t + 1);

  if (sessions === null || classes === null) return <Empty>Loading…</Empty>;

  const nDays = Math.round((to - from) / 86400000);
  const days = [...Array(nDays)].map((_, i) => { const d = new Date(from); d.setDate(d.getDate() + i); return d; });
  const todayStr = new Date().toDateString();

  const monthLabel = _calAnchor.toLocaleDateString([], { month: 'long', year: 'numeric' });
  const label = mode === 'month'
    ? monthLabel
    : `${days[0].toLocaleDateString([], { day: 'numeric', month: 'short' })} – ${days[6].toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })}`;

  const years = [];
  const thisYear = new Date().getFullYear();
  for (let y = thisYear - 2; y <= thisYear + 2; y++) years.push(y);
  const months = [...Array(12)].map((_, m) => new Date(2000, m, 1).toLocaleDateString([], { month: 'long' }));

  const openSchedule = async (ts = null, classId = null) => {
    const [classesAll, instructors] = await Promise.all([api('/classes'), api('/instructors')]);
    if (!classesAll.length) return toast('Create a class first', 'bad');
    open(<SessionForm classes={classesAll} instructors={instructors} presetTs={ts} presetClassId={classId} onSaved={reload} />);
  };
  const openRepeat = async () => {
    const [classesAll, instructors] = await Promise.all([api('/classes'), api('/instructors')]);
    if (!classesAll.length) return toast('Create a class first', 'bad');
    open(<RepeatSessions classes={classesAll} instructors={instructors} onSaved={reload} />);
  };

  const shift = n => {
    const d = new Date(_calAnchor);
    if (mode === 'month') d.setMonth(d.getMonth() + n); else d.setDate(d.getDate() + n * 7);
    _calAnchor = d;
    bump();
  };
  const goToday = () => { _calAnchor = new Date(); bump(); };
  const setMode = m => { _calMode = m; bump(); };
  const jump = (monthIdx, year) => {
    _calAnchor = new Date(year, monthIdx, 1);
    if (_calMode === 'auto') _calMode = narrow ? 'agenda' : 'month';
    bump();
  };

  let weekBounds = null;
  if (mode !== 'agenda' && mode !== 'month') {
    let lo = 8; let hi = 21;
    for (const s of sessions) {
      const d = new Date(s.starts_at * 1000);
      lo = Math.min(lo, d.getHours());
      hi = Math.max(hi, Math.ceil(d.getHours() + (d.getMinutes() / 60) + s.duration_hours));
    }
    lo = Math.max(0, lo - 1); hi = Math.min(24, hi + 1);
    const hours = []; for (let h = lo; h < hi; h++) hours.push(h);
    weekBounds = { hours, lo, hi };
  }

  return (
    <>
      <div className="head">
        <div>
          <h1>Calendar</h1>
          <div className="sub">{label} · {sessions.length} session{sessions.length === 1 ? '' : 's'}</div>
        </div>
        <div className="row">
          <button className="pri" onClick={() => openSchedule()}>Add session</button>
          <button onClick={openRepeat}>Repeat weekly</button>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 16, gap: 8 }}>
        <button onClick={() => shift(-1)} aria-label="Previous">‹</button>
        <button onClick={goToday}>Today</button>
        <button onClick={() => shift(1)} aria-label="Next">›</button>

        <select
          style={{ width: 'auto', minWidth: 130 }} value={_calAnchor.getMonth()}
          onChange={e => jump(Number(e.target.value), _calAnchor.getFullYear())}
        >
          {months.map((m, i) => <option key={i} value={i}>{m}</option>)}
        </select>
        <select
          style={{ width: 'auto', minWidth: 90 }} value={_calAnchor.getFullYear()}
          onChange={e => jump(_calAnchor.getMonth(), Number(e.target.value))}
        >
          {years.map(y => <option key={y} value={y}>{y}</option>)}
        </select>

        <div style={{ flex: 1 }} />
        <div className="row tight">
          <button className={mode === 'week' ? 'pri' : ''} onClick={() => setMode('week')}>Week</button>
          <button className={mode === 'month' ? 'pri' : ''} onClick={() => setMode('month')}>Month</button>
          <button className={mode === 'agenda' ? 'pri' : ''} onClick={() => setMode('agenda')}>List</button>
        </div>
      </div>

      {mode === 'agenda' && (
        <AgendaList
          days={days} sessions={sessions} todayStr={todayStr}
          onRowClick={id => nav(`/session/${id}`)} onAddClick={ts => openSchedule(ts)}
        />
      )}
      {mode === 'month' && (
        <>
          <MonthGrid
            days={days} sessions={sessions} todayStr={todayStr} month={_calAnchor.getMonth()}
            onCellClick={ts => openSchedule(ts)} onEventClick={id => nav(`/session/${id}`)}
          />
          <div className="sub" style={{ marginTop: 10 }}>Tap a day to schedule a class on it.</div>
        </>
      )}
      {weekBounds && (
        <>
          <WeekGrid
            days={days} sessions={sessions} hours={weekBounds.hours} todayStr={todayStr}
            lo={weekBounds.lo} hi={weekBounds.hi}
            onSlotClick={ts => openSchedule(ts)} onEventClick={id => nav(`/session/${id}`)}
          />
          <div className="sub" style={{ marginTop: 10 }}>Tap an empty slot to schedule a class there.</div>
        </>
      )}

      <div className="row" style={{ marginTop: 16 }}>
        {classes.map(c => (
          <span key={c.id} className="pill grey">
            <span className="dot" style={{ background: c.colour, width: 8, height: 8, margin: 0 }} />{c.name}
          </span>
        ))}
      </div>
    </>
  );
}
