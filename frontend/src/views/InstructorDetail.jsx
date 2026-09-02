import { useNavigate, useParams } from 'react-router-dom';

import { api, useApi } from '../api';
import { fmtFull, fmtISO, hrs, initials } from '../lib/format';
import { useModal } from '../components/Modal';
import { useConfirm } from '../components/ConfirmModal';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import { Pill } from '../components/Pill';
import Empty from '../components/Empty';
import InstructorForm from '../modals/InstructorForm';

function sessionColumns() {
  return [
    { label: 'WHEN', sortValue: r => r.starts_at, cell: r => fmtFull(r.starts_at) },
    {
      label: 'CLASS',
      sortValue: r => r.class_name,
      cell: r => <><span className="dot" style={{ background: r.colour }} />{r.class_name}</>,
    },
    {
      label: 'LENGTH', className: 'mute', hideSm: true, sortValue: r => r.duration_hours,
      cell: r => hrs(r.duration_hours),
    },
    { label: 'ATTENDED', className: 'num', sortValue: r => r.attended, cell: r => r.attended },
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

export default function InstructorDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { data: i, loading, error, reload } = useApi(`/instructors/${id}`);
  const { open } = useModal();
  const confirm = useConfirm();
  const toast = useToast();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const t = i.totals;
  const now = Date.now() / 1000;
  const upcoming = i.sessions
    .filter(x => x.starts_at >= now && x.status === 'scheduled')
    .sort((a, b) => a.starts_at - b.starts_at);
  const past = i.sessions.filter(x => x.starts_at < now);

  const archive = () => confirm({
    title: 'Archive instructor',
    message: 'They will be hidden from the list. Any upcoming session they are assigned to must be reassigned first.',
    label: 'Archive',
    onConfirm: async () => {
      await api(`/instructors/${i.id}`, { method: 'DELETE' });
      toast('Instructor archived');
      nav('/instructors');
    },
  });

  return (
    <>
      <div className="head">
        <div className="row" style={{ gap: 16 }}>
          <span className="avatar lg">{initials(i.name)}</span>
          <div>
            <div className="eyebrow">Instructor</div>
            <h1>{i.name}</h1>
            <div className="sub">{i.specialty || ''}{i.phone ? ` · ${i.phone}` : ''}</div>
          </div>
        </div>
        <div className="row">
          <button onClick={() => open(<InstructorForm existing={i} onSaved={reload} />)}>Edit</button>
          <button className="danger" onClick={archive}>Archive</button>
        </div>
      </div>

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <div className="box kpi">
          <div className="k">SESSIONS TAUGHT</div><div className="v">{t.sessions_taught}</div>
          <div className="n">{t.upcoming} still upcoming</div>
        </div>
        <div className="box kpi">
          <div className="k">HOURS TAUGHT</div>
          <div className="v" style={{ color: 'var(--ok)' }}>{t.hours_taught}</div>
          <div className="n">{t.upcoming_hours} h scheduled</div>
        </div>
        <div className="box kpi">
          <div className="k">RATE</div>
          <div className="v" style={{ fontSize: 22, paddingTop: 6 }}>{t.hourly_rate.toLocaleString()}</div>
          <div className="n">EGP per hour</div>
        </div>
        <div className="box kpi">
          <div className="k">EARNED</div>
          <div className="v" style={{ fontSize: 22, paddingTop: 6, color: 'var(--brand-deep)' }}>
            {t.earned.toLocaleString()}
          </div>
          <div className="n">EGP · {t.upcoming_value.toLocaleString()} upcoming</div>
        </div>
      </div>

      {i.logged && i.logged.days ? (
        <div className="grid g2" style={{ marginBottom: 16 }}>
          <div className="box kpi">
            <div className="k">HOURS ON THE SALARY SHEET</div>
            <div className="v" style={{ color: 'var(--brand)' }}>{i.logged.hours}</div>
            <div className="n">{i.logged.days} days worked · {fmtISO(i.logged.from)} to {fmtISO(i.logged.to)}</div>
          </div>
          <div className="box kpi">
            <div className="k">PAY FOR THOSE HOURS</div>
            <div className="v" style={{ fontSize: 22, paddingTop: 6, color: 'var(--brand-deep)' }}>
              {i.logged.pay.toLocaleString()} <span style={{ fontSize: 12, color: 'var(--mute)' }}>EGP</span>
            </div>
            <div className="n">at {t.hourly_rate.toLocaleString()} EGP per hour</div>
          </div>
        </div>
      ) : null}

      <h2>Upcoming sessions ({upcoming.length})</h2>
      {upcoming.length ? (
        <div className="box pad0">
          <DataTable
            rows={upcoming} rowKey={r => r.id} search="Search sessions…"
            onRowClick={r => nav(`/session/${r.id}`)}
            columns={sessionColumns()}
          />
        </div>
      ) : <div className="box"><Empty>Nothing scheduled.</Empty></div>}

      <h2>Past sessions ({past.length})</h2>
      {past.length ? (
        <div className="box pad0">
          <DataTable
            rows={past.slice(0, 40)} rowKey={r => r.id} search="Search sessions…"
            onRowClick={r => nav(`/session/${r.id}`)}
            columns={sessionColumns()}
          />
        </div>
      ) : <div className="box"><Empty>No sessions taught yet.</Empty></div>}
    </>
  );
}
