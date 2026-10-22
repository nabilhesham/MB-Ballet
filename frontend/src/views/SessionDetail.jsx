import { useNavigate, useParams } from 'react-router-dom';

import { api, useApi } from '../api';
import { checkinSource, fmtFull, fmtTime, fmtWhen, hrs, sinceStart } from '../lib/format';
import { useModal } from '../components/Modal';
import { useConfirm } from '../components/ConfirmModal';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import { StatusPill } from '../components/Pill';
import Avatar from '../components/Avatar';
import Notes from '../components/Notes';
import Classes from '../components/Classes';
import Empty from '../components/Empty';
import SessionForm from '../modals/SessionForm';
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
  const ended = s.ends_at < now;
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
            {/* The end time, from the stored `ends_at` and nothing else.
                That column is the alarm the end-of-session event is set by
                (see db.py), so it is the one number that decides when this
                class closes and its no-shows are written off. Printing it
                beside the length is what makes a row where the two
                disagree visible on the screen somebody opens to ask why. */}
            {fmtFull(s.starts_at)} – {fmtTime(s.ends_at)} ·{' '}
            {hrs(s.duration_hours)} ·{' '}
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
      {/* Booking someone in happens on their profile, not here: a booking
          spends a slot on one of that client's plans, so the screen that
          makes it should be the one showing what they have left. This page
          takes attendance for people already booked. */}
      <div className="sub" style={{ marginBottom: 12 }}>
        Present and absent both use the client's slot — the place was reserved either way.
        Book someone in from their own profile, where their plan and its free slots are.
      </div>

      <div className="box pad0 dt-host">
        <DataTable
          rows={s.roster} rowKey={r => r.id} search="Search students…"
          empty={<><strong>Nobody booked in</strong>Assign this session from a client's plan, on their profile.</>}
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
            {
              /* The same column as the Clients list and the class roster —
                 every class they are in, so one client reads the same
                 wherever reception meets them. */
              label: 'CLASS', className: 'mute', hideSm: true,
              sortValue: r => (r.classes || []).map(c => c.name).join(', '),
              cell: r => <Classes rows={r.classes} />,
            },
            { label: 'STATUS', sortValue: r => r.status, cell: r => <StatusPill status={r.status} /> },
            {
              label: 'CHECKED IN', className: 'num mute', hideSm: true,
              sortValue: r => r.checked_in_at || r.settled_at || 0,
              // fmtWhen, not fmtTime: this stamp is when somebody pressed
              // Present, which for a roster marked in advance is another day
              // altogether. A bare "08:11 PM" beside a 3:30 class reads as a
              // fault in the app rather than as a mark made last night.
              //
              // An absence the event wrote carries its own stamp, and it
              // belongs here beside the session's end time in the header:
              // "absent 03:20 PM" against a class running to 05:00 PM is the
              // whole of what three reports of "marked absent too early" had
              // no way to show. Blank on an absence marked by hand or born
              // with a back-dated plan — neither is the event's.
              //
              // The tooltip carries the full stamp and how long after this
              // session started it falls, which is the one reading a wrong
              // timezone cannot change and a wrong clock cannot hide — see
              // lib/format.js's sinceStart().
              cell: (r) => {
                const ts = r.checked_in_at || r.settled_at;
                if (!ts) return '—';
                // A present-mark reads as what it is. "marked 05:10 PM" is
                // the roster being taken; a bare time is the client at the
                // desk. Same column, two different events — see
                // lib/format.js's CHECKIN_SOURCE.
                const src = r.checked_in_at ? checkinSource(r.checked_in_by) : null;
                const text = r.checked_in_at
                  ? `${src.prefix}${fmtWhen(r.checked_in_at)}`
                  : `absent ${fmtWhen(r.settled_at)}`;
                const what = src ? src.said : 'written off by the absent sweep';
                return (
                  <span title={`${fmtFull(ts)} — ${what}; ${sinceStart(ts, s.starts_at)}`}>
                    {text}
                  </span>
                );
              },
            },
            {
              /* The client's own note. Same column as the Clients list and
                 the class roster, so one client reads the same wherever
                 reception meets them — and this is the screen where
                 attendance is actually taken, which is when a note about the
                 person is worth something. */
              label: 'NOTES', className: 'mute', hideSm: true,
              sortValue: r => r.notes || '', cell: r => <Notes text={r.notes} />,
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
