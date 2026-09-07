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

export default function Sessions() {
  const now = Math.floor(Date.now() / 1000);
  const { data: list, loading, error, reload } = useApi(`/sessions?start=${now - 21 * 86400}&end=${now + 42 * 86400}`);
  const { open } = useModal();
  const confirm = useConfirm();
  const toast = useToast();
  const nav = useNavigate();
  const [selected, setSelected] = useState(() => new Set());

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const upcoming = list.filter(s => s.starts_at >= now - 3600);
  const past = list.filter(s => s.starts_at < now - 3600).slice().reverse();

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
        <div><h1>Sessions</h1><div className="sub">{upcoming.length} upcoming</div></div>
        <div className="row">
          <button onClick={openRepeat}>Repeat weekly</button>
          <button className="pri" onClick={openSchedule}>Add session</button>
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
          onRowClick={r => nav(`/session/${r.id}`)} empty="Nothing scheduled." columns={columns}
        />
      </div>

      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h2>Past three weeks</h2>
        {past.length > 0 && (
          <button className="sm" onClick={() => selectAll(past)}>Select all past</button>
        )}
      </div>
      <div className="box pad0 dt-host">
        <DataTable
          rows={past} rowKey={r => r.id} search="Search by class, instructor or status…"
          onRowClick={r => nav(`/session/${r.id}`)} empty="No past sessions." columns={columns}
        />
      </div>
    </>
  );
}
