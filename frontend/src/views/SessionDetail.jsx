import { useNavigate, useParams } from 'react-router-dom';

import { api, useApi } from '../api';
import { fmtFull, fmtTime, hrs } from '../lib/format';
import { useModal } from '../components/Modal';
import { useConfirm } from '../components/ConfirmModal';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import { StatusPill } from '../components/Pill';
import Avatar from '../components/Avatar';
import Empty from '../components/Empty';
import SessionForm from '../modals/SessionForm';
import AddStudents from '../modals/AddStudents';
import DeleteSessionWithAttendance from '../modals/DeleteSessionWithAttendance';

export default function SessionDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { data: s, loading, error, reload } = useApi(`/sessions/${id}`);
  const { open } = useModal();
  const confirm = useConfirm();
  const toast = useToast();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const now = Date.now() / 1000;
  const ended = s.starts_at + s.duration_hours * 3600 < now;
  const present = s.roster.filter(m => m.status === 'present').length;
  const absent = s.roster.filter(m => m.status === 'absent').length;
  const pending = s.roster.filter(m => m.status === 'booked').length;

  const markAs = async (cid, status) => {
    try {
      await api(`/sessions/${s.id}/status-of`, { method: 'POST', body: { client_id: cid, status } });
      toast(status === 'present' ? 'Marked present' : 'Marked absent');
      reload();
    } catch (e) { toast(e.message, 'bad'); }
  };

  const openEdit = async () => {
    const [classes, instructors] = await Promise.all([api('/classes'), api('/instructors')]);
    open(<SessionForm session={s} classes={classes} instructors={instructors} onSaved={reload} />);
  };

  const openAddStudent = async () => {
    const all = await api('/clients');
    const inSession = new Set(s.roster.map(m => m.id));
    if (!all.some(c => !inSession.has(c.id))) return toast('Every client is already booked in', 'bad');
    open(<AddStudents sessionId={s.id} roster={s.roster} allClients={all} onSaved={reload} />);
  };

  const unbook = (cid, name) => confirm({
    title: 'Remove from this session',
    message: <><b>{name}</b> comes off this session and the slot returns to their plan.</>,
    label: 'Remove',
    onConfirm: async () => {
      await api(`/sessions/${s.id}/book/${cid}`, { method: 'DELETE' });
      toast('Removed');
      reload();
    },
  });

  const cancelSession = () => confirm({
    title: 'Cancel this session',
    message: (
      <><b>{s.class_name}</b> will be marked cancelled and every booked slot returns to
        the clients, so nobody pays for a class that did not run.</>
    ),
    label: 'Cancel the session',
    onConfirm: async () => {
      const r = await api(`/sessions/${s.id}/cancel`, { method: 'POST' });
      toast(`Cancelled — ${r.released} slot(s) returned`);
      reload();
    },
  });

  const reopen = async () => {
    await api(`/sessions/${s.id}/status/scheduled`, { method: 'PUT' });
    toast('Session scheduled');
    reload();
  };

  const forceDelete = async () => {
    try {
      const r = await api(`/sessions/${s.id}?force=true`, { method: 'DELETE' });
      toast(`Session deleted — ${r.released} slot(s) returned`);
      nav('/sessions');
    } catch (e) { toast(e.message, 'bad'); }
  };

  const deleteSession = () => {
    const marked = s.roster.filter(m => m.status !== 'booked').length;
    if (!marked) {
      confirm({
        title: 'Delete this session',
        message: (
          <><b>{s.class_name}</b> on {fmtFull(s.starts_at)} will be removed and
            {' '}{s.roster.length} booked slot(s) returned. Nobody has been marked yet.</>
        ),
        label: 'Delete',
        onConfirm: async () => {
          await api(`/sessions/${s.id}`, { method: 'DELETE' });
          toast('Session deleted');
          nav('/sessions');
        },
      });
      return;
    }
    open(
      <DeleteSessionWithAttendance
        className={s.class_name} markedCount={marked}
        onCancelInstead={cancelSession} onForceDelete={forceDelete}
      />,
    );
  };

  return (
    <>
      <div className="head">
        <div>
          <div className="eyebrow">Session</div>
          <h1><span className="dot" style={{ background: s.colour, width: 13, height: 13 }} />{s.class_name}</h1>
          <div className="sub">
            {fmtFull(s.starts_at)} · {hrs(s.duration_hours)} ·{' '}
            <a href={`#/class/${s.class_id}`} style={{ color: 'var(--brand-deep)' }}>view class</a>
          </div>
        </div>
        <div className="row">
          <button onClick={openEdit}>Edit session</button>
          {s.status !== 'cancelled'
            ? <button className="danger" onClick={cancelSession}>Cancel</button>
            : <button onClick={reopen}>Reopen</button>}
          <button className="danger" onClick={deleteSession}>Delete</button>
        </div>
      </div>

      {ended && pending > 0 && (
        <div className="warnline">
          This session has finished and {pending} {pending === 1 ? 'person is' : 'people are'} still
          unmarked. They will be counted absent automatically.
        </div>
      )}

      <div className="grid g4" style={{ marginBottom: 20 }}>
        <div
          className={'box kpi' + (s.instructor_id ? ' tap' : '')}
          onClick={s.instructor_id ? () => nav(`/instructor/${s.instructor_id}`) : undefined}
        >
          <div className="k">INSTRUCTOR</div>
          <div className="v" style={{ fontSize: 18, paddingTop: 8 }}>{s.instructor_name || '— none —'}</div>
          <div className="n">{s.instructor_id ? 'view their schedule ›' : 'assign one from Edit session'}</div>
        </div>
        <div className="box kpi">
          <div className="k">PRESENT</div>
          <div className="v" style={{ color: 'var(--ok)' }}>{present}</div>
          <div className="n">of {s.roster.length} booked</div>
        </div>
        <div className="box kpi">
          <div className="k">ABSENT</div>
          <div className="v" style={{ color: absent ? 'var(--bad)' : 'var(--ink)' }}>{absent}</div>
          <div className="n">{pending ? `${pending} still to mark` : 'all marked'}</div>
        </div>
        <div className="box kpi">
          <div className="k">LENGTH</div>
          <div className="v" style={{ fontSize: 20, paddingTop: 8 }}>{hrs(s.duration_hours)}</div>
          <div className="n">{s.status}</div>
        </div>
      </div>

      <h2>Who is booked in</h2>
      <div className="sub" style={{ marginBottom: 12 }}>
        Present and absent both use the client's slot — the place was reserved either way.
      </div>

      <div className="row" style={{ marginBottom: 12 }}>
        <button className="pri" onClick={openAddStudent}>Add student</button>
      </div>

      <div className="box pad0 dt-host">
        <DataTable
          rows={s.roster} rowKey={r => r.id} search="Search students…"
          empty={<><strong>Nobody booked in</strong>Add a student, or assign this session from a client's plan.</>}
          columns={[
            { label: '', sortable: false, style: { width: 54 }, cell: r => <Avatar client={r} /> },
            {
              label: 'NAME', sortValue: r => r.name_en, onCellClick: r => nav(`/client/${r.id}`),
              cell: r => r.name_en,
            },
            {
              label: 'MOBILE', className: 'num mute', hideSm: true, sortValue: r => r.phone || '',
              cell: r => r.phone || '—',
            },
            { label: 'STATUS', sortValue: r => r.status, cell: r => <StatusPill status={r.status} /> },
            {
              label: 'CHECKED IN', className: 'num mute', hideSm: true, sortValue: r => r.checked_in_at || 0,
              cell: r => (r.checked_in_at ? fmtTime(r.checked_in_at) : '—'),
            },
            {
              label: 'MARK AS', sortable: false,
              cell: r => (
                <div className="row tight">
                  <button className={'sm' + (r.status === 'present' ? ' pri' : '')}
                          onClick={() => markAs(r.id, 'present')}>Present</button>
                  <button className={'sm' + (r.status === 'absent' ? ' danger' : '')}
                          onClick={() => markAs(r.id, 'absent')}>Absent</button>
                </div>
              ),
            },
            {
              label: '', sortable: false, className: 'right',
              cell: r => (
                <button className="sm ghost" title="Remove from this session"
                        onClick={() => unbook(r.id, r.name_en)}>✕</button>
              ),
            },
          ]}
        />
      </div>
    </>
  );
}
