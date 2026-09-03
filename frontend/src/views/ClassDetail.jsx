import { useNavigate, useParams } from 'react-router-dom';

import { api, useApi } from '../api';
import { fmtFull, hrs } from '../lib/format';
import { useModal } from '../components/Modal';
import { useConfirm } from '../components/ConfirmModal';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import { Pill } from '../components/Pill';
import Avatar from '../components/Avatar';
import Empty from '../components/Empty';
import ClassForm from '../modals/ClassForm';
import SessionForm from '../modals/SessionForm';
import RepeatSessions from '../modals/RepeatSessions';

export default function ClassDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { data: c, loading, error, reload } = useApi(`/classes/${id}`);
  const { open } = useModal();
  const confirm = useConfirm();
  const toast = useToast();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const now = Date.now() / 1000;
  const upcoming = c.sessions.filter(x => x.starts_at >= now && x.status === 'scheduled');

  const openSchedule = async () => {
    const [classes, instructors] = await Promise.all([api('/classes'), api('/instructors')]);
    if (!classes.length) return toast('Create a class first', 'bad');
    open(<SessionForm classes={classes} instructors={instructors} presetClassId={c.id} onSaved={reload} />);
  };
  const openRepeat = async () => {
    const [classes, instructors] = await Promise.all([api('/classes'), api('/instructors')]);
    if (!classes.length) return toast('Create a class first', 'bad');
    open(<RepeatSessions classes={classes} instructors={instructors} presetClassId={c.id} onSaved={reload} />);
  };
  const openEditClass = async () => {
    const instructors = await api('/instructors');
    open(<ClassForm existing={c} instructors={instructors} onSaved={reload} />);
  };
  const archive = () => confirm({
    title: 'Archive class',
    message: (
      <><b>{c.name}</b> will be hidden from the list. Any upcoming sessions are released — the
        clients booked into them keep the slot as unassigned on their plan. Past sessions and
        every attendance record are kept exactly as they are.</>
    ),
    label: 'Archive',
    onConfirm: async () => {
      const r = await api(`/classes/${c.id}`, { method: 'DELETE' });
      toast(r.released_sessions
        ? `Class archived — ${r.released_sessions} upcoming session${r.released_sessions === 1 ? '' : 's'} released`
        : 'Class archived');
      nav('/classes');
    },
  });

  return (
    <>
      <div className="head">
        <div>
          <div className="eyebrow">Class</div>
          <h1><span className="dot" style={{ background: c.colour, width: 13, height: 13 }} />{c.name}</h1>
          <div className="sub">{hrs(c.duration_hours)}{c.level ? ` · ${c.level}` : ''}</div>
        </div>
        <div className="row">
          <button onClick={openEditClass}>Edit class</button>
          <button onClick={openSchedule}>Schedule session</button>
          <button onClick={openRepeat}>Repeat weekly</button>
        </div>
      </div>

      <div className="grid g4" style={{ marginBottom: 20 }}>
        <div className="box kpi">
          <div className="k">STUDENTS</div><div className="v">{c.students.length}</div>
          <div className="n">with a booking</div>
        </div>
        <div className="box kpi">
          <div className="k">UPCOMING</div><div className="v">{upcoming.length}</div>
          <div className="n">sessions scheduled</div>
        </div>
        <div className="box kpi">
          <div className="k">HELD</div>
          <div className="v">{c.sessions.filter(x => x.status === 'completed').length}</div>
          <div className="n">sessions completed</div>
        </div>
        <div className="box kpi">
          <div className="k">DURATION</div>
          <div className="v" style={{ fontSize: 20, paddingTop: 8 }}>{hrs(c.duration_hours)}</div>
          <div className="n">default per session</div>
        </div>
      </div>

      {c.description && (
        <div className="box" style={{ marginBottom: 20, color: 'var(--mute)' }}>{c.description}</div>
      )}

      <h2>Sessions</h2>
      <div className="sub" style={{ marginBottom: 12 }}>
        Each session has its own instructor, defaulting to the class's — change one from the
        session page any time.
      </div>
      <div className="box pad0 dt-host">
        <DataTable
          rows={c.sessions} rowKey={r => r.id} search="Search sessions…"
          onRowClick={r => nav(`/session/${r.id}`)}
          empty={<><strong>No sessions scheduled</strong>Use Schedule session, or Repeat weekly for a whole term.</>}
          columns={[
            { label: 'WHEN', sortValue: r => r.starts_at, cell: r => fmtFull(r.starts_at) },
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
            { label: '', sortable: false, className: 'right', cell: () => <span style={{ color: 'var(--dim)' }}>›</span> },
          ]}
        />
      </div>

      <h2>Students ({c.students.length})</h2>
      <div className="sub" style={{ marginBottom: 12 }}>
        Anyone with a booking in this class. Membership follows the bookings, so there is no separate enrolment list.
      </div>
      <div className="box pad0 dt-host">
        <DataTable
          rows={c.students} rowKey={r => r.id} search="Search students…"
          onRowClick={r => nav(`/client/${r.id}`)}
          empty="Nobody booked into this class yet."
          columns={[
            { label: '', sortable: false, style: { width: 54 }, cell: r => <Avatar client={r} /> },
            { label: 'NAME', sortValue: r => r.name_en, cell: r => r.name_en },
            {
              label: 'MOBILE', className: 'num', hideSm: true, sortValue: r => r.phone || '',
              cell: r => r.phone || '—',
            },
            { label: 'SLOTS', className: 'num', sortValue: r => r.slots, cell: r => r.slots },
            { label: 'ATTENDED', className: 'num', sortValue: r => r.attended || 0, cell: r => r.attended || 0 },
          ]}
        />
      </div>

      <div className="row" style={{ marginTop: 22 }}>
        <button className="danger" onClick={archive}>Archive class</button>
      </div>
    </>
  );
}
