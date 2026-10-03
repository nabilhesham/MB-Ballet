import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useApi } from '../api';
import { fmtISODay } from '../lib/format';
import { useModal } from '../components/Modal';
import DataTable from '../components/DataTable';
import Avatar from '../components/Avatar';
import { BalancePill, Pill } from '../components/Pill';
import Empty from '../components/Empty';
import ClientForm from '../modals/ClientForm';

/*
 * The one list in the app with its own search bar instead of <DataTable>'s
 * built-in one: filtering here is a server round-trip (`/api/clients?q=`),
 * not an in-memory filter of already-loaded rows, because the client list is
 * the one place that can be genuinely large. Debounced 250ms, same as
 * static/app.js. Sorting is still client-side over whatever page of results
 * came back — static/app.js's generic enhanceTables() pass made every table
 * sortable regardless of whether it opted into search, this one included —
 * so <DataTable> is used here too, just without its `search` prop.
 *
 * The old version had to manually refocus the search input after each
 * re-render, because it replaced view.innerHTML wholesale — destroying and
 * recreating the <input> on every keystroke. React reconciles the same
 * <input> across renders, so the input is simply never unmounted and needs
 * no such workaround.
 */
export default function Clients() {
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  // When they first joined. Draft and applied are kept apart and the range
  // lands on a button, never on change: a date input fires on every edit, so
  // "2" on the way to "2026" would reload the list for the year 2. The same
  // split the Sessions range, the dashboard's period and the Cards day
  // filter use.
  const [draft, setDraft] = useState({ from: '', to: '' });
  const [range, setRange] = useState({ from: '', to: '' });
  const { open } = useModal();
  const nav = useNavigate();

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 250);
    return () => clearTimeout(t);
  }, [query]);

  const joined = (range.from ? `&joined_from=${range.from}` : '')
    + (range.to ? `&joined_to=${range.to}` : '');
  const { data: list, loading, error, reload } =
    useApi(`/clients?q=${encodeURIComponent(debounced)}${joined}`);

  const dirty = draft.from !== range.from || draft.to !== range.to;
  const filtered = Boolean(range.from || range.to);
  const applyRange = () => setRange({ ...draft });
  const clearRange = () => { setDraft({ from: '', to: '' }); setRange({ from: '', to: '' }); };

  if (loading && list === null) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  return (
    <>
      <div className="head">
        <div><h1>Clients</h1></div>
        <div className="row">
          <button onClick={() => nav('/clients/archived')}>Archived</button>
          <button
            className="pri"
            onClick={() => open(<ClientForm onSaved={reload} onCreated={id => nav(`/client/${id}`)} />)}
          >
            New client
          </button>
        </div>
      </div>

      <div className="box pad0 dt-host">
        <div className="dt-bar">
          <input
            className="search dt-find" type="search"
            placeholder="Search name, mobile or school…"
            value={query} onChange={e => setQuery(e.target.value)}
          />
          {/* Beside the search rather than in a band of its own above it:
              both act on the same list, so one row of controls reads as one
              control. Same place the Cards screen's day filter sits. */}
          <span className="dt-day">
            <label htmlFor="joinedFrom">JOINED FROM</label>
            <input id="joinedFrom" type="date" value={draft.from}
                   onChange={e => setDraft(p => ({ ...p, from: e.target.value }))} />
            <label htmlFor="joinedTo">TO</label>
            <input id="joinedTo" type="date" value={draft.to}
                   onChange={e => setDraft(p => ({ ...p, to: e.target.value }))} />
            <button className="sm pri" onClick={applyRange} disabled={!dirty}>Apply</button>
            <button className="sm" onClick={clearRange}
                    disabled={!filtered && !dirty}>Show all</button>
          </span>
          <span className="dt-count">{list.length} client{list.length === 1 ? '' : 's'}</span>
        </div>
        <DataTable
          rows={list} rowKey={r => r.id} onRowClick={r => nav(`/client/${r.id}`)}
          empty="No clients found."
          columns={[
            { label: '', sortable: false, style: { width: 54 }, cell: r => <Avatar client={r} /> },
            { label: 'NAME', sortValue: r => r.name_en, cell: r => r.name_en },
            {
              label: 'MOBILE', className: 'num', sortValue: r => r.phone || '',
              cell: r => (r.phone
                ? (
                  <a href={`tel:${r.phone}`} style={{ color: 'var(--brand-deep)' }}
                     onClick={e => e.stopPropagation()}>{r.phone}</a>
                )
                : <span className="mute">—</span>),
            },
            {
              label: 'AGE', className: 'num mute', hideSm: true, sortValue: r => r.age ?? -1,
              cell: r => r.age ?? '—',
            },
            {
              label: 'SCHOOL', className: 'mute', hideSm: true, sortValue: r => r.school || '',
              cell: r => r.school || '—',
            },
            {
              /* The column the range above filters on. A filter over
                 something the table cannot show is a list that quietly
                 disagrees with itself; it also sorts, which is the other
                 thing anybody asks of a joining date. */
              label: 'FIRST JOINED', className: 'mute num', hideSm: true,
              sortValue: r => r.joined_on || '',
              cell: r => (r.joined_on ? fmtISODay(r.joined_on) : '—'),
            },
            { label: 'PLAN', className: 'mute', sortValue: r => r.plan || '', cell: r => r.plan || '—' },
            {
              label: 'LEFT',
              sortValue: r => (r.frozen ? -2 : (r.unassigned > 0 ? -1 : (r.remaining ?? 999))),
              cell: r => (r.frozen
                ? <Pill kind="info">frozen{r.frozen_until ? ` to ${r.frozen_until}` : ''}</Pill>
                : r.unassigned > 0
                  ? <Pill kind="warn">{r.unassigned} unassigned</Pill>
                  : <BalancePill row={r} />),
            },
            {
              label: 'CARDS', sortValue: r => r.cards || 0,
              cell: r => (r.cards ? <Pill kind="ok">{r.cards}</Pill> : <Pill kind="warn">none</Pill>),
            },
          ]}
        />
      </div>
    </>
  );
}
