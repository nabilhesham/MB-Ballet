import { useNavigate } from 'react-router-dom';

import { useApi } from '../api';
import DataTable from '../components/DataTable';
import Avatar from '../components/Avatar';
import { Pill, BalancePill } from '../components/Pill';
import Empty from '../components/Empty';

export default function Cards() {
  const { data: list, loading, error } = useApi('/clients?status=attention');
  const nav = useNavigate();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const issue = c => {
    if (!c.cards) return <Pill kind="warn">no card</Pill>;
    if (c.expired) return <Pill kind="bad">plan expired</Pill>;
    if (c.unassigned > 0) return <Pill kind="warn">{c.unassigned} unassigned</Pill>;
    if (c.empty) return <Pill kind="bad">no sessions</Pill>;
    if (c.low) return <Pill kind="warn">running low</Pill>;
    return <Pill kind="grey">—</Pill>;
  };

  return (
    <>
      <div className="head">
        <div>
          <h1>Cards &amp; renewals</h1>
          <div className="sub">Clients who need a card, a renewal, or have sessions still unassigned</div>
        </div>
      </div>

      <div className="box pad0 dt-host">
        <DataTable
          rows={list} rowKey={r => r.id} search="Search clients…"
          empty="Nothing needs attention."
          columns={[
            { label: '', sortable: false, style: { width: 54 }, cell: r => <Avatar client={r} /> },
            {
              label: 'NAME', sortValue: r => r.name_en, onCellClick: r => nav(`/client/${r.id}`),
              cell: r => r.name_en,
            },
            { label: 'MOBILE', className: 'mute num', sortValue: r => r.phone || '', cell: r => r.phone || '—' },
            { label: 'ISSUE', sortable: false, cell: issue },
            { label: 'LEFT', sortable: false, cell: r => <BalancePill row={r} /> },
            {
              label: '', sortable: false, className: 'right',
              cell: r => <button className="sm" onClick={() => nav(`/client/${r.id}`)}>Open</button>,
            },
          ]}
        />
      </div>
    </>
  );
}
