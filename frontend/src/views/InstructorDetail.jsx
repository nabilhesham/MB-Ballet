import { useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { api, useApi } from '../api';
import { fmtFull, fmtISO, hrs, isoDay, thisMonthBounds } from '../lib/format';
import { useModal } from '../components/Modal';
import { useConfirm } from '../components/ConfirmModal';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import { Pill } from '../components/Pill';
import Avatar from '../components/Avatar';
import Empty from '../components/Empty';
import InstructorForm from '../modals/InstructorForm';
import EditInstructorHours from '../modals/EditInstructorHours';

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

// The search box matches literal text on the row's own values (DataTable's
// rowSearchText) — starts_at is a raw unix number, so a typed date can't
// match it without a plain string field carrying one alongside it.
const withDateSearch = list => list.map(s => ({ ...s, _search_date: `${isoDay(s.starts_at)} ${fmtFull(s.starts_at)}` }));

export default function InstructorDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const [[defaultFrom, defaultTo]] = useState(thisMonthBounds);
  // What the inputs hold, and what the page is actually showing, kept apart
  // on purpose: a date input fires on every change, so binding the request
  // straight to it reloaded the whole view mid-edit — once for a half-typed
  // year, again for the real one. Nothing moves until Apply.
  const [draft, setDraft] = useState({ from: defaultFrom, to: defaultTo });
  const [range, setRange] = useState({ from: defaultFrom, to: defaultTo });
  const { data: i, loading, error, reload } =
    useApi(`/instructors/${id}?from=${range.from}&to=${range.to}`);
  const { open } = useModal();
  const confirm = useConfirm();
  const toast = useToast();
  const photoInput = useRef(null);

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const t = i.totals;
  const now = Date.now() / 1000;
  const upcoming = i.sessions
    .filter(x => x.starts_at >= now && x.status === 'scheduled')
    .sort((a, b) => a.starts_at - b.starts_at);
  const past = i.sessions.filter(x => x.starts_at < now);

  const resetPeriod = () => {
    setDraft({ from: defaultFrom, to: defaultTo });
    setRange({ from: defaultFrom, to: defaultTo });
  };
  const applyPeriod = () => {
    if (!draft.from || !draft.to) return toast('Pick both dates', 'bad');
    if (draft.from > draft.to) return toast('The start date is after the end date', 'bad');
    return setRange(draft);
  };
  const dirty = draft.from !== range.from || draft.to !== range.to;

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

  const uploadPhoto = () => photoInput.current?.click();
  const onPhotoChosen = async e => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`/api/instructors/${i.id}/photo`, { method: 'POST', body: fd });
    if (r.ok) { toast('Photo saved'); reload(); } else toast('Upload failed', 'bad');
  };

  const openEditHours = () => {
    open(
      <EditInstructorHours
        instructorId={i.id} from={i.period_from} to={i.period_to}
        currentTotal={i.logged.hours} onSaved={reload}
      />,
    );
  };

  return (
    <>
      <div className="head">
        <div className="row" style={{ gap: 16 }}>
          <Avatar client={i} big />
          <div>
            <div className="eyebrow">Instructor</div>
            <h1>{i.name}</h1>
            <div className="sub">{i.specialty || ''}{i.phone ? ` · ${i.phone}` : ''}</div>
          </div>
        </div>
        <div className="row">
          <input type="file" accept="image/*" ref={photoInput} onChange={onPhotoChosen} hidden />
          <button onClick={uploadPhoto}>Photo</button>
          <button onClick={() => open(<InstructorForm existing={i} onSaved={reload} />)}>Edit</button>
          <button className="danger" onClick={archive}>Archive</button>
        </div>
      </div>

      <div className="filterbar" style={{ margin: '0 0 16px' }}>
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
        <button className="pri" onClick={applyPeriod} disabled={!dirty}>Apply</button>
        <button onClick={resetPeriod}>Reset to this month</button>
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

      <div className="grid g2" style={{ marginBottom: 16 }}>
        <div className="box kpi">
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
            <div className="k">HOURS ON THE SALARY SHEET</div>
            <button className="sm" onClick={openEditHours}>Edit</button>
          </div>
          <div className="v" style={{ color: 'var(--brand)' }}>{i.logged.hours}</div>
          <div className="n">
            {i.logged.days
              ? <>{i.logged.days} days worked · {fmtISO(i.logged.from)} to {fmtISO(i.logged.to)}</>
              : 'No salary-sheet days in this period'}
          </div>
        </div>
        <div className="box kpi">
          <div className="k">PAY FOR THOSE HOURS</div>
          <div className="v" style={{ fontSize: 22, paddingTop: 6, color: 'var(--brand-deep)' }}>
            {i.logged.pay.toLocaleString()} <span style={{ fontSize: 12, color: 'var(--mute)' }}>EGP</span>
          </div>
          <div className="n">at {t.hourly_rate.toLocaleString()} EGP per hour</div>
        </div>
      </div>

      <h2>Upcoming sessions ({upcoming.length})</h2>
      {upcoming.length ? (
        <div className="box pad0">
          <DataTable
            rows={withDateSearch(upcoming)} rowKey={r => r.id} search="Search sessions…"
            onRowClick={r => nav(`/session/${r.id}`)}
            columns={sessionColumns()}
          />
        </div>
      ) : <div className="box"><Empty>Nothing scheduled in this period.</Empty></div>}

      <h2>Past sessions ({past.length})</h2>
      {past.length ? (
        <div className="box pad0">
          <DataTable
            rows={withDateSearch(past.slice(0, 40))} rowKey={r => r.id} search="Search sessions…"
            onRowClick={r => nav(`/session/${r.id}`)}
            columns={sessionColumns()}
          />
        </div>
      ) : <div className="box"><Empty>No sessions taught in this period.</Empty></div>}
    </>
  );
}
