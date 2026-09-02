import { useNavigate } from 'react-router-dom';

import { api, useApi } from '../api';
import { fmtFull, hrs } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import { Pill } from '../components/Pill';
import Empty from '../components/Empty';
import SessionForm from '../modals/SessionForm';
import RepeatSessions from '../modals/RepeatSessions';

function sessionColumns() {
  return [
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
  const toast = useToast();
  const nav = useNavigate();

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

  const columns = sessionColumns();

  return (
    <>
      <div className="head">
        <div><h1>Sessions</h1><div className="sub">{upcoming.length} upcoming</div></div>
        <div className="row">
          <button onClick={openRepeat}>Repeat weekly</button>
          <button className="pri" onClick={openSchedule}>Add session</button>
        </div>
      </div>

      <h2>Upcoming</h2>
      <div className="box pad0 dt-host">
        <DataTable
          rows={upcoming} rowKey={r => r.id} search="Search by class, instructor or status…"
          onRowClick={r => nav(`/session/${r.id}`)} empty="Nothing scheduled." columns={columns}
        />
      </div>

      <h2>Past three weeks</h2>
      <div className="box pad0 dt-host">
        <DataTable
          rows={past} rowKey={r => r.id} search="Search by class, instructor or status…"
          onRowClick={r => nav(`/session/${r.id}`)} empty="No past sessions." columns={columns}
        />
      </div>
    </>
  );
}
