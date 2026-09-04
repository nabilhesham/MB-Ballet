import { useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { api, useApi } from '../api';
import { fmtDate, fmtFull, fmtTime } from '../lib/format';
import { useModal } from '../components/Modal';
import { useConfirm } from '../components/ConfirmModal';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import Avatar from '../components/Avatar';
import { PaidPill, Pill, StatusPill } from '../components/Pill';
import Empty from '../components/Empty';
import ClientForm from '../modals/ClientForm';
import PlanPicker from '../modals/PlanPicker';
import AssignRemaining from '../modals/AssignRemaining';
import AddSessionToPlan from '../modals/AddSessionToPlan';
import PlanSessionsPopup from '../modals/PlanSessionsPopup';
import EditAttendance from '../modals/EditAttendance';
import MoveBooking from '../modals/MoveBooking';
import FreezePlan from '../modals/FreezePlan';
import EditPlan from '../modals/EditPlan';

export default function ClientDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { data: c, loading, error, reload } = useApi(`/clients/${id}`);
  const { open } = useModal();
  const confirm = useConfirm();
  const toast = useToast();
  const photoInput = useRef(null);

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  /* The profile is organised by class, because that is how the money works:
     one plan per class, paying for that class's sessions, proved by that
     class's card. Totals across the top are the sum of those plans. */
  const plans = c.active_plans || [];
  const cardFor = cid => c.cards.find(x => x.class_id === cid);
  const totalLeft = plans.reduce((n, p) => n + p.remaining, 0);
  const totalOf = plans.reduce((n, p) => n + p.sessions_total, 0);
  const attended = plans.reduce((n, p) => n + p.present, 0);
  const absent = plans.reduce((n, p) => n + p.absent, 0);
  // Adding a session only makes sense for a class whose plan still has a
  // paid slot with no date on it. A frozen plan is skipped too: freezing is
  // what created the unassigned slot, and it stays off-limits until the
  // plan is active again.
  const addableClasses = (c.classes_enrolled || []).filter(k => k.unassigned > 0 && !k.frozen);

  const uploadPhoto = () => photoInput.current?.click();
  const onPhotoChosen = async e => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`/api/clients/${c.id}/photo`, { method: 'POST', body: fd });
    if (r.ok) { toast('Photo saved'); reload(); } else toast('Upload failed', 'bad');
  };

  const archive = () => confirm({
    title: 'Archive client',
    message: (
      <><b>{c.name_en}</b> will be hidden and their cards revoked. Any future bookings
        are released; their attendance history is kept.</>
    ),
    label: 'Archive',
    onConfirm: async () => {
      await api(`/clients/${c.id}`, { method: 'DELETE' });
      toast('Client archived');
      nav('/clients');
    },
  });

  const issueCard = async classId => {
    try {
      const r = await api(`/clients/${c.id}/card`, { method: 'POST', body: { class_id: classId } });
      toast(r.revoked ? 'New card issued — old one revoked' : 'Card issued');
      reload();
    } catch (e) { toast(e.message, 'bad'); }
  };

  const openNewPlan = async (presetClassId = null) => {
    const classes = await api('/classes');
    if (!classes.length) return toast('Create a class first', 'bad');
    open(<PlanPicker clientId={c.id} presetClassId={presetClassId} classes={classes} onSaved={reload} />, { wide: true });
  };

  const openAssignRemaining = plan => {
    open(<AssignRemaining clientId={c.id} plan={plan} onSaved={reload} />, { wide: true });
  };

  const openEditPlan = plan => {
    open(<EditPlan clientId={c.id} plan={plan} onSaved={reload} />, { wide: true });
  };

  const openAddSession = () => {
    if (!addableClasses.length) return toast('Every plan already has all its sessions assigned', 'bad');
    open(<AddSessionToPlan clientId={c.id} classesEnrolled={addableClasses} onSaved={reload} />, { wide: true });
  };

  const openPlanSessions = pl => {
    open(<PlanSessionsPopup clientId={c.id} planId={pl.id} name={pl.plan} paidOn={pl.paid_on} />, { wide: true });
  };

  const openMove = async (fromSessionId, classId) => {
    const now = Math.floor(Date.now() / 1000);
    const list = await api(`/sessions?start=${now}&end=${now + 180 * 86400}&class_id=${classId}&available_for=${c.id}`);
    if (!list.length) return toast('No other sessions of this class to move to', 'bad');
    open(<MoveBooking clientId={c.id} fromSessionId={fromSessionId} sessions={list} onSaved={reload} />);
  };

  const dropBooking = (sessionId) => confirm({
    title: 'Release this session',
    message: 'The slot returns to their plan and can be assigned to another date.',
    label: 'Release',
    onConfirm: async () => {
      await api(`/sessions/${sessionId}/book/${c.id}`, { method: 'DELETE' });
      toast('Slot released');
      reload();
    },
  });

  const openEditAttendance = h => {
    open(
      <EditAttendance
        clientId={c.id} sessionId={h.session_id} className={h.class_name}
        status={h.status} ts={h.starts_at} onSaved={reload}
      />,
    );
  };

  const openFreeze = plan => {
    open(<FreezePlan planId={plan.id} clientName={c.name_en} className={plan.class_name} onSaved={reload} />);
  };

  const unfreeze = planId => confirm({
    title: 'Unfreeze this plan',
    message: (
      <>The expiry date moves out by the length of the pause. Any sessions released
        when it was frozen come back as unassigned, so you will need to give them
        new dates.</>
    ),
    label: 'Unfreeze',
    danger: false,
    onConfirm: async () => {
      const r = await api(`/plans/${planId}/unfreeze`, { method: 'POST' });
      toast(`Unfrozen — ${r.days} day${r.days === 1 ? '' : 's'} added, now ends ${r.expires_on}`);
      reload();
    },
  });

  return (
    <>
      <div className="head">
        <div className="row" style={{ gap: 16 }}>
          <Avatar client={c} big />
          <div>
            <div className="eyebrow">MEMBER {String(c.id).padStart(5, '0')}</div>
            <h1>{c.name_en}</h1>
            <div className="row" style={{ marginTop: 9, gap: 8 }}>
              {c.phone
                ? <a className="pill brand" href={`tel:${c.phone}`} style={{ textDecoration: 'none' }}>{c.phone}</a>
                : <Pill kind="warn">no mobile number</Pill>}
              {c.age ? <Pill kind="grey">{c.age} years old</Pill> : null}
              {c.school ? <Pill kind="grey">{c.school}</Pill> : null}
              {c.joined_on ? <Pill kind="grey">joined {c.joined_on}</Pill> : null}
            </div>
          </div>
        </div>
        <div className="row">
          <input type="file" accept="image/*" ref={photoInput} onChange={onPhotoChosen} hidden />
          <button onClick={uploadPhoto}>Photo</button>
          <button onClick={() => open(<ClientForm existing={c} onSaved={reload} />)}>Edit</button>
          <button className="pri" onClick={() => openNewPlan()}>Add plan</button>
          <button className="danger" onClick={archive}>Archive</button>
        </div>
      </div>

      <div className="grid g4" style={{ marginBottom: 22 }}>
        <div className="box kpi">
          <div className="k">SESSIONS LEFT</div>
          <div
            className="v"
            style={{ color: !plans.length ? 'var(--mute)' : totalLeft <= 0 ? 'var(--bad)' : totalLeft <= 2 ? 'var(--warn)' : 'var(--ok)' }}
          >
            {plans.length ? totalLeft : '—'}
          </div>
          <div className="n">
            {plans.length ? `of ${totalOf} across ${plans.length} plan${plans.length === 1 ? '' : 's'}` : 'no active plan'}
          </div>
        </div>
        <div className="box kpi">
          <div className="k">CLASSES</div><div className="v">{plans.length}</div>
          <div className="n">{plans.map(p => p.class_name || '—').join(', ') || 'none'}</div>
        </div>
        <div className="box kpi">
          <div className="k">ATTENDED</div>
          <div className="v" style={{ color: 'var(--ok)' }}>{attended}</div>
          <div className="n">across active plans</div>
        </div>
        <div className="box kpi">
          <div className="k">ABSENT</div>
          <div className="v" style={{ color: absent ? 'var(--bad)' : 'var(--ink)' }}>{absent}</div>
          <div className="n">across active plans</div>
        </div>
      </div>

      <h2>Plans and cards</h2>
      <div className="sub" style={{ marginBottom: 12 }}>
        A plan is bought for one class and pays only for that class's sessions.
        Its card checks the client into those sessions and nothing else.
      </div>

      {plans.length ? plans.map(p => {
        const card = cardFor(p.class_id);
        return (
          <div key={p.id} className="box" style={{ borderLeft: `3px solid ${p.class_colour || '#87438E'}`, marginBottom: 14 }}>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 600 }}>
                  <span className="dot" style={{ background: p.class_colour || '#87438E' }} />
                  {p.class_name || 'No class'}{' '}
                  <PaidPill paidOn={p.paid_on} />{' '}
                  {p.frozen ? <Pill kind="info">frozen</Pill> : null}
                </div>
                <div className="sub">
                  {p.plan} · ends {p.expires_on}
                  {p.frozen_days ? ` · ${p.frozen_days}d added by freezes` : ''}
                </div>
              </div>
              <div className="row tight">
                {p.frozen
                  ? <button className="sm" disabled title="Unfreeze this plan before editing it">Edit</button>
                  : <button className="sm" onClick={() => openEditPlan(p)}>Edit</button>}
                {p.frozen
                  ? <button className="sm" onClick={() => unfreeze(p.id)}>Unfreeze</button>
                  : (p.can_freeze
                    ? <button className="sm" onClick={() => openFreeze(p)}>Freeze</button>
                    : <button className="sm" disabled title={p.freeze_blocked_because}>Freeze</button>)}
                <button className="sm" onClick={() => openNewPlan(p.class_id)}>Renew</button>
              </div>
            </div>

            <div className="row" style={{ marginTop: 14, gap: 16 }}>
              <div>
                <div className="eyebrow" style={{ margin: 0 }}>LEFT</div>
                <div style={{ fontSize: 20, fontWeight: 600, color: p.remaining <= 0 ? 'var(--bad)' : p.remaining <= 2 ? 'var(--warn)' : 'var(--ok)' }}>
                  {p.remaining}<span className="sub"> of {p.sessions_total}</span>
                </div>
              </div>
              <div>
                <div className="eyebrow" style={{ margin: 0 }}>ATTENDED</div>
                <div style={{ fontSize: 20, fontWeight: 600 }}>{p.present}</div>
              </div>
              <div>
                <div className="eyebrow" style={{ margin: 0 }}>ABSENT</div>
                <div style={{ fontSize: 20, fontWeight: 600 }}>{p.absent}</div>
              </div>
              <div style={{ flex: 1 }} />
              <div style={{ textAlign: 'right' }}>
                <div className="eyebrow" style={{ margin: 0 }}>CARD</div>
                {card ? (
                  <div className="row tight" style={{ marginTop: 5 }}>
                    <a className="btn sm" href={card.card_url} download>Download</a>
                    <button className="sm" onClick={() => window.open(card.card_url)}>Print</button>
                    <button className="sm" onClick={() => issueCard(p.class_id)}>Reissue</button>
                  </div>
                ) : (
                  <div className="row tight" style={{ marginTop: 5 }}>
                    <button className="sm pri" onClick={() => issueCard(p.class_id)}>Issue card</button>
                  </div>
                )}
              </div>
            </div>

            {p.frozen && (
              <div className="frozenline" style={{ margin: '14px 0 0' }}>
                <b>Frozen</b> since {p.frozen_on}
                {p.frozen_until ? <> — lifts on <b>{p.frozen_until}</b></> : ' — until you lift it'}.
                Scanning this card is refused and the expiry moves out by the length of the pause.
              </div>
            )}

            {p.unassigned > 0 && !p.frozen && (
              <div className="warnline" style={{ margin: '14px 0 0' }}>
                {p.unassigned} of these {p.sessions_total} sessions {p.unassigned === 1 ? 'has' : 'have'}
                {' '}no date yet.{' '}
                <a href="#" onClick={e => { e.preventDefault(); openAssignRemaining(p); }}
                   style={{ color: 'var(--warn)', fontWeight: 600 }}>Assign them now</a>.
              </div>
            )}
          </div>
        );
      }) : (
        <div className="box"><Empty title="No active plan">Add one to book sessions and issue a card.</Empty></div>
      )}

      <div className="grid g2" style={{ marginTop: 8 }}>
        <div>
          <h2>Payment history</h2>
          <div className="sub" style={{ marginBottom: 10 }}>Every plan bought. Click a row to see the sessions it paid for.</div>
          <div className="box pad0 dt-host">
            <DataTable
              rows={c.plans} rowKey={r => r.id} onRowClick={openPlanSessions}
              empty="No plans yet."
              columns={[
                {
                  label: 'CLASS', sortValue: r => r.class_name || '',
                  cell: r => <><span className="dot" style={{ background: r.class_colour || '#ccc' }} />{r.class_name || '—'}</>,
                },
                {
                  label: 'PLAN', sortValue: r => r.plan,
                  cell: r => (
                    <>
                      {r.plan}
                      {r.active ? <> <Pill kind="ok">active</Pill></> : null}
                      {r.frozen ? <> <Pill kind="info">frozen</Pill></> : null}
                    </>
                  ),
                },
                {
                  label: 'PERIOD', className: 'mute num', hideSm: true, sortValue: r => r.starts_on,
                  cell: r => `${r.starts_on} → ${r.expires_on}`,
                },
                { label: 'USED', className: 'num', sortValue: r => r.used, cell: r => `${r.used}/${r.sessions_total}` },
                {
                  label: 'PRICE', className: 'num mute', sortValue: r => r.price ?? -1,
                  cell: r => (r.price ? r.price.toLocaleString() : '—'),
                },
                {
                  // Sorts unpaid plans together at one end rather than
                  // scattering them among the dates.
                  label: 'PAID', sortValue: r => r.paid_on || '',
                  cell: r => <PaidPill paidOn={r.paid_on} />,
                },
              ]}
            />
          </div>
        </div>

        <div>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline', margin: '32px 0 14px' }}>
            <h2 style={{ margin: 0 }}>Upcoming sessions</h2>
            {addableClasses.length
              ? <button className="sm" onClick={openAddSession}>Add session</button>
              : (
                <button
                  className="sm" disabled
                  title="Every plan already has all its sessions assigned — move or remove an existing one instead"
                >
                  Add session
                </button>
              )}
          </div>
          <div className="sub" style={{ marginBottom: 10 }}>
            Click one to move it to another date of the same class.
            {addableClasses.length ? '' : ' Every plan already has all its sessions assigned, so none can be added here — move or remove an existing one instead.'}
          </div>
          <div className="box pad0 dt-host">
            <DataTable
              rows={c.upcoming} rowKey={r => r.booking_id}
              empty="No upcoming sessions booked."
              columns={[
                {
                  label: 'WHEN', sortValue: r => r.starts_at,
                  cell: r => <>{fmtFull(r.starts_at)}<div className="sub">{r.instructor_name || 'no instructor'}</div></>,
                },
                {
                  label: 'CLASS', sortValue: r => r.class_name,
                  cell: r => <><span className="dot" style={{ background: r.colour }} />{r.class_name}</>,
                },
                {
                  label: '', sortable: false, className: 'right',
                  cell: r => (
                    <div className="row tight" style={{ justifyContent: 'flex-end' }}>
                      <button className="sm" onClick={() => openMove(r.session_id, r.class_id)}>Move</button>
                      <button className="sm ghost" onClick={() => dropBooking(r.session_id)}>✕</button>
                    </div>
                  ),
                },
              ]}
            />
          </div>

          <h2>Attendance history</h2>
          <div className="sub" style={{ marginBottom: 10 }}>Click a row to correct whether they attended.</div>
          <div className="box pad0 dt-host">
            <DataTable
              rows={c.history} rowKey={r => r.booking_id} search="Search attendance…"
              onRowClick={openEditAttendance}
              empty="No past sessions."
              columns={[
                {
                  label: 'DATE', className: 'num mute', sortValue: r => r.starts_at,
                  cell: r => `${fmtDate(r.starts_at)} ${fmtTime(r.starts_at)}`,
                },
                {
                  label: 'CLASS', sortValue: r => r.class_name,
                  cell: r => <><span className="dot" style={{ background: r.colour }} />{r.class_name}</>,
                },
                { label: 'RESULT', sortValue: r => r.status, cell: r => <StatusPill status={r.status} /> },
              ]}
            />
          </div>
        </div>
      </div>
    </>
  );
}
