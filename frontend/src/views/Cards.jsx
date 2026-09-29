import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useApi } from '../api';
import DataTable from '../components/DataTable';
import Avatar from '../components/Avatar';
import { Pill, BalancePill } from '../components/Pill';
import Empty from '../components/Empty';

export default function Cards() {
  // The day filter, and the same draft/applied split the dashboard's period
  // and the instructor page's range use: a date input fires on every edit,
  // so binding the request straight to it would reload the list for a
  // half-typed year. Empty is the standing list — everyone who needs a card,
  // a renewal, or has slots nobody has written dates against.
  const [draft, setDraft] = useState('');
  const [day, setDay] = useState('');
  const { data: list, loading, error } =
    useApi('/clients?status=attention' + (day ? `&on=${day}` : ''));
  const nav = useNavigate();

  const apply = () => setDay(draft);
  const clear = () => { setDraft(''); setDay(''); };

  const issue = c => {
    // With a day chosen the row is on the list *because* of one of these
    // three, so saying which is the whole point of the column. Without one
    // it is the standing list and the old reasons still apply.
    if (day) {
      return (
        <>
          {c.ran_out ? <Pill kind="bad">ran out</Pill> : null}
          {c.renew ? <Pill kind="bad">plan ends</Pill> : null}
          {c.one_left ? <Pill kind="warn">1 left</Pill> : null}
        </>
      );
    }
    if (!c.cards) return <Pill kind="warn">no card</Pill>;
    if (c.expired) return <Pill kind="bad">plan expired</Pill>;
    if (c.unassigned > 0) return <Pill kind="warn">{c.unassigned} unassigned</Pill>;
    if (c.empty) return <Pill kind="bad">no sessions</Pill>;
    if (c.low) return <Pill kind="warn">running low</Pill>;
    return <Pill kind="grey">—</Pill>;
  };

  // The filter lives inside the table's own bar, beside the search box, so
  // the two controls acting on one list read as one control.
  const bar = (
    <span className="dt-day">
      <label htmlFor="attnDay">ON DAY</label>
      <input id="attnDay" type="date" value={draft}
             onChange={e => setDraft(e.target.value)} />
      <button className="sm pri" onClick={apply} disabled={draft === day}>Apply</button>
      <button className="sm" onClick={clear} disabled={!day && !draft}>Show all</button>
    </span>
  );

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  return (
    <>
      <div className="head">
        <div>
          <h1>Cards &amp; renewals</h1>
          <div className="sub">
            {day
              ? 'Plans ending that day, plans whose last session falls on it, and plans down to one'
              : 'Clients who need a card, a renewal, or have sessions still unassigned'}
          </div>
        </div>
      </div>

      <div className="box pad0 dt-host">
        <DataTable
          rows={list} rowKey={r => r.id} search="Search clients…" bar={bar}
          empty={day ? 'Nobody on that day.' : 'Nothing needs attention.'}
          columns={[
            { label: '', sortable: false, style: { width: 54 }, cell: r => <Avatar client={r} /> },
            {
              label: 'NAME', sortValue: r => r.name_en, onCellClick: r => nav(`/client/${r.id}`),
              cell: r => r.name_en,
            },
            { label: 'MOBILE', className: 'mute num', sortValue: r => r.phone || '', cell: r => r.phone || '—' },
            { label: day ? 'ON THAT DAY' : 'ISSUE', sortable: false, cell: issue },
            // The balance stands down when a day is chosen: the column left
            // of it is then the same number with the reason attached, and
            // "1 left · 1 left" across two columns reads as a fault.
            day ? null
                : { label: 'LEFT', sortable: false, cell: r => <BalancePill row={r} /> },
            {
              label: '', sortable: false, className: 'right',
              cell: r => <button className="sm" onClick={() => nav(`/client/${r.id}`)}>Open</button>,
            },
          ].filter(Boolean)}
        />
      </div>
    </>
  );
}
