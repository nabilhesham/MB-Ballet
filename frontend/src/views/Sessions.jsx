import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, useApi } from '../api';
import { fmtFull, hrs } from '../lib/format';
import { useModal } from '../components/Modal';
import { useConfirm } from '../components/ConfirmModal';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import { Pill } from '../components/Pill';
import Empty from '../components/Empty';
import SessionForm from '../modals/SessionForm';
import RepeatSessions from '../modals/RepeatSessions';

/* The checkbox column relies on DataTable's own onCellClick, which stops the
   click reaching the row — so ticking a box never also opens the session. */
function sessionColumns(selected, toggle) {
  return [
    {
      label: '', sortable: false, style: { width: 40 }, onCellClick: r => toggle(r.id),
      cell: r => (
        <input type="checkbox" readOnly checked={selected.has(r.id)} style={{ width: 'auto' }} />
      ),
    },
    { label: 'WHEN', sortValue: r => r.starts_at, cell: r => fmtFull(r.starts_at) },
    {
      label: 'CLASS', sortValue: r => r.class_name,
      cell: r => <><span className="dot" style={{ background: r.colour }} />{r.class_name}</>,
    },
    {
      label: 'INSTRUCTOR', className: 'mute', sortValue: r => r.instructor_name || '',
      cell: r => r.instructor_name || '— none —',
    },
    {
      label: 'LENGTH', className: 'mute', hideSm: true, sortValue: r => r.duration_hours,
      cell: r => hrs(r.duration_hours),
    },
    {
      label: 'ATTENDED', className: 'num', sortValue: r => (r.booked ? r.attended / r.booked : 0),
      cell: r => `${r.attended}/${r.booked}`,
    },
    {
      label: 'STATUS', sortValue: r => r.status,
      cell: r => (
        <Pill kind={r.status === 'cancelled' ? 'bad' : r.status === 'completed' ? 'grey' : 'info'}>
          {r.status}
        </Pill>
      ),
    },
  ];
}

/* A day as the epoch seconds it spans. The list holds `starts_at` as a
   timestamp, so a typed date has to become one to compare against it — and
   the TO bound is the *end* of that day, or picking the same date for both
   would match nothing but midnight. */
function dayStart(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).getTime() / 1000;
}
function dayEnd(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d, 23, 59, 59).getTime() / 1000;
}

export default function Sessions() {
  const now = Math.floor(Date.now() / 1000);
  // Every session, not a window. This list is the one place a session can be
  // deleted, so anything it cannot show is a session nobody can remove — and
  // the calendar was still showing those, and slot_conflict() was still
  // refusing to schedule over them. Repeat weekly writes twelve weeks at a
  // time, so half of every batch used to land outside the old nine-week
  // window the moment it was created.
  const { data: list, loading, error, reload } = useApi('/sessions');
  const { open } = useModal();
  const confirm = useConfirm();
  const toast = useToast();
  const nav = useNavigate();
  const [selected, setSelected] = useState(() => new Set());
  // Two states, not one: `draft` is what the date inputs hold and `range` is
  // what the tables are filtered by. A date input fires on every edit, so
  // binding the filter straight to it empties the list while a year is still
  // half typed — the same reason the instructor page's range applies on a
  // button. See CLAUDE.md.
  const [draft, setDraft] = useState({ from: '', to: '' });
  const [range, setRange] = useState({ from: '', to: '' });

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  // The one thing this list could not be searched by. DataTable's search box
  // matches the row's own values, and a session's date is an epoch integer
  // in there, so typing "12 Sep" found nothing — on the one screen that
  // shows the whole timetable and is the only place a session can be
  // deleted from. The text box stays for class, instructor and status.
  const dirty = draft.from !== range.from || draft.to !== range.to;
  const inRange = s => (!range.from || s.starts_at >= dayStart(range.from))
    && (!range.to || s.starts_at <= dayEnd(range.to));
  const shown = list.filter(inRange);
  const filtered = range.from || range.to;

  const upcoming = shown.filter(s => s.starts_at >= now - 3600);
  const past = shown.filter(s => s.starts_at < now - 3600).slice().reverse();

  const openRepeat = async () => {
    const [classes, instructors] = await Promise.all([api('/classes'), api('/instructors')]);
    if (!classes.length) return toast('Create a class first', 'bad');
    open(<RepeatSessions classes={classes} instructors={instructors} onSaved={reload} />);
  };
  const openSchedule = async () => {
    const [classes, instructors] = await Promise.all([api('/classes'), api('/instructors')]);
    if (!classes.length) return toast('Create a class first', 'bad');
    open(<SessionForm classes={classes} instructors={instructors} onSaved={reload} />);
  };

  const toggle = id => setSelected(s => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const selectAll = xs => setSelected(s => new Set([...s, ...xs.map(x => x.id)]));

  const deleteSelected = () => {
    const ids = [...selected];
    const withAttendance = list.filter(s => ids.includes(s.id) && s.attended > 0).length;
    confirm({
      title: `Delete ${ids.length} session${ids.length === 1 ? '' : 's'}`,
      message: (
        <>
          {ids.length === 1 ? 'This session' : 'These sessions'} and any bookings on
          {ids.length === 1 ? ' it' : ' them'} will be removed, and the slots go back to the
          clients' plans as unassigned.
          {withAttendance > 0 && (
            <> {withAttendance} of them {withAttendance === 1 ? 'has' : 'have'} attendance
              recorded and <b>will be kept</b> — cancel {withAttendance === 1 ? 'it' : 'those'} from
              the session page instead if that is what you want.</>
          )}
        </>
      ),
      label: 'Delete',
      onConfirm: async () => {
        try {
          const r = await api('/sessions/bulk-delete', { method: 'POST', body: { ids } });
          setSelected(new Set());
          const kept = r.blocked.length
            ? `, ${r.blocked.length} kept (attendance recorded)` : '';
          toast(`${r.deleted} deleted${kept}`, r.blocked.length ? 'bad' : undefined);
          reload();
        } catch (e) { toast(e.message, 'bad'); }
      },
    });
  };

  const columns = sessionColumns(selected, toggle);

  return (
    <>
      <div className="head">
        <div>
          <h1>Sessions</h1>
          <div className="sub">
            {upcoming.length} upcoming · {past.length} past
            {filtered
              ? <> — {shown.length} of {list.length} sessions, in the dates picked</>
              : <> — the whole timetable, the same sessions the calendar shows</>}
          </div>
        </div>
        <div className="row">
          <button onClick={openRepeat}>Repeat weekly</button>
          <button className="pri" onClick={openSchedule}>Add session</button>
        </div>
      </div>

      {/* Two .filterbar rows, the same shape the instructor page uses: the
          dates together on their own line, the buttons acting on them under
          it, both at the same height. */}
      <div style={{ margin: '0 0 16px' }}>
        <div className="filterbar">
          <div>
            <label>FROM</label>
            <input type="date" value={draft.from}
                   onChange={e => setDraft(d => ({ ...d, from: e.target.value }))} />
          </div>
          <div>
            <label>TO</label>
            <input type="date" value={draft.to}
                   onChange={e => setDraft(d => ({ ...d, to: e.target.value }))} />
          </div>
        </div>
        <div className="filterbar" style={{ marginTop: 10 }}>
          <button className="pri" onClick={() => setRange(draft)} disabled={!dirty}>Apply</button>
          <button onClick={() => { setDraft({ from: '', to: '' }); setRange({ from: '', to: '' }); }}
                  disabled={!filtered && !draft.from && !draft.to}>Show all</button>
        </div>
      </div>

      {selected.size > 0 && (
        <div className="warnline row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <span><b>{selected.size}</b> session{selected.size === 1 ? '' : 's'} selected</span>
          <span className="row tight">
            <button className="sm" onClick={() => setSelected(new Set())}>Clear</button>
            <button className="sm danger" onClick={deleteSelected}>Delete selected</button>
          </span>
        </div>
      )}

      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h2>Upcoming</h2>
        {upcoming.length > 0 && (
          <button className="sm" onClick={() => selectAll(upcoming)}>Select all upcoming</button>
        )}
      </div>
      <div className="box pad0 dt-host">
        <DataTable
          rows={upcoming} rowKey={r => r.id} search="Search by class, instructor or status…"
          onRowClick={r => nav(`/session/${r.id}`)} empty={filtered ? "Nothing scheduled in those dates." : "Nothing scheduled."} columns={columns}
        />
      </div>

      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h2>Past</h2>
        {past.length > 0 && (
          <button className="sm" onClick={() => selectAll(past)}>Select all past</button>
        )}
      </div>
      <div className="box pad0 dt-host">
        <DataTable
          rows={past} rowKey={r => r.id} search="Search by class, instructor or status…"
          onRowClick={r => nav(`/session/${r.id}`)} empty={filtered ? "No past sessions in those dates." : "No past sessions."} columns={columns}
        />
      </div>
    </>
  );
}
