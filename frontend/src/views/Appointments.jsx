import { useEffect, useState } from 'react';

import { api, useApi } from '../api';
import { fmtISODayTime } from '../lib/format';
import { useModal } from '../components/Modal';
import DataTable from '../components/DataTable';
import Empty from '../components/Empty';
import { useConfirm } from '../components/ConfirmModal';
import { useToast } from '../components/Toast';
import AppointmentForm from '../modals/AppointmentForm';

/*
 * Enquiries: people who have asked to come in and are not clients yet.
 *
 * Both filters are server-side, which is why this view has a hand-built
 * search bar rather than <DataTable>'s `search` prop — the same call the
 * Clients list makes, and for the same reason. A second, client-side filter
 * next to a server-filtered result would be filtering a different set of
 * rows than the one just fetched.
 *
 * It matters more here than on Clients, because the date range is
 * server-side too: an in-memory text search would be searching only the
 * rows that survived the range, and the two boxes would silently mean
 * different things.
 */
export default function Appointments() {
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  // Two states, not one, exactly as the Sessions list does it: `draft` is
  // what the date inputs hold and `range` is what has been asked for. A date
  // input fires on every edit, so binding the request straight to it
  // refetches for a half-typed year. See the .filterbar note in CLAUDE.md.
  const [draft, setDraft] = useState({ from: '', to: '' });
  const [range, setRange] = useState({ from: '', to: '' });
  const { open } = useModal();
  const confirm = useConfirm();
  const toast = useToast();

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 250);
    return () => clearTimeout(t);
  }, [query]);

  const qs = new URLSearchParams({ q: debounced });
  if (range.from) qs.set('date_from', range.from);
  if (range.to) qs.set('date_to', range.to);
  const { data: list, loading, error, reload } = useApi(`/appointments?${qs}`);

  const edit = a => open(<AppointmentForm appt={a} onSaved={reload} />);

  /* Deleted outright, which is the one place in this app that is the right
     answer -- an enquiry has no attendance, no plan and no card behind it,
     so there is nothing to lose by removing it and a list full of
     cancelled calls is worse at the only job it has. See the endpoint. */
  const remove = a => confirm({
    title: 'Delete this appointment',
    message: <>The enquiry from <b>{a.name}</b> will be removed for good. Nothing
      else is affected — an appointment is only a note that somebody rang up.</>,
    label: 'Delete it',
    onConfirm: async () => {
      await api(`/appointments/${a.id}`, { method: 'DELETE' });
      toast('Appointment deleted');
      reload();
    },
  });

  if (loading && list === null) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const dirty = draft.from !== range.from || draft.to !== range.to;
  const filtered = range.from || range.to;

  return (
    <>
      <div className="head">
        <div><h1>Appointments</h1></div>
        <div className="row">
          <button className="pri" onClick={() => open(<AppointmentForm onSaved={reload} />)}>
            New appointment
          </button>
        </div>
      </div>

      {/* Two .filterbar rows, the same shape the Sessions and instructor
          pages use: the dates on their own line, the buttons acting on them
          under it, both pinned to the same height. */}
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

      <div className="box pad0 dt-host">
        <div className="dt-bar">
          <input
            className="search dt-find" type="search"
            placeholder="Search name or mobile…"
            value={query} onChange={e => setQuery(e.target.value)}
          />
          <span className="dt-count">
            {list.length} appointment{list.length === 1 ? '' : 's'}
            {filtered ? ' in range' : ''}
          </span>
        </div>
        <DataTable
          rows={list} rowKey={r => r.id}
          empty={query || filtered
            ? 'No appointments match that.'
            : 'No appointments yet. Book the first one.'}
          columns={[
            { label: 'NAME', sortValue: r => r.name, cell: r => r.name },
            {
              label: 'AGE', className: 'num', sortValue: r => r.age ?? -1,
              cell: r => (r.age == null ? '—' : r.age),
            },
            {
              label: 'MOBILE', className: 'num', sortValue: r => r.phone || '',
              cell: r => (r.phone
                ? <a href={`tel:${r.phone}`} style={{ color: 'var(--brand-deep)' }}>{r.phone}</a>
                : <span className="mute">— none —</span>),
            },
            {
              // Sorted on the two halves joined, so rows on the same day
              // order by time of day and the ones with no time sit first --
              // the order the day itself runs in.
              label: 'APPOINTMENT', sortValue: r => `${r.on_date} ${r.on_time || ''}`,
              cell: r => fmtISODayTime(r.on_date, r.on_time),
            },
            {
              label: 'NOTES', className: 'mute', hideSm: true,
              sortValue: r => r.notes || '', cell: r => r.notes || '',
            },
            {
              // No sortValue: a column of buttons has nothing to sort by,
              // and that absence is what marks it unsortable -- see the
              // <DataTable> note in CLAUDE.md.
              label: '', className: 'right',
              cell: r => (
                <div className="row" style={{ gap: 6, justifyContent: 'flex-end' }}>
                  <button className="sm" onClick={() => edit(r)}>Edit</button>
                  <button className="sm danger" onClick={() => remove(r)}>Delete</button>
                </div>
              ),
            },
          ]}
        />
      </div>
    </>
  );
}
