import { useNavigate } from 'react-router-dom';

import { api, useApi } from '../api';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import Avatar from '../components/Avatar';
import Empty from '../components/Empty';

/** Archived clients, with a way back — see Clients.jsx's "Archived" button. */
export default function ArchivedClients() {
  const { data: list, loading, error, reload } = useApi('/clients?status=archived');
  const nav = useNavigate();
  const toast = useToast();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const unarchive = async c => {
    await api(`/clients/${c.id}/unarchive`, { method: 'POST' });
    toast('Client restored');
    reload();
  };

  return (
    <>
      <div className="head">
        <div>
          <h1>Archived clients</h1>
          <div className="sub">
            {list.length} archived — restoring brings the client and their history back, but
            not their card: archiving revokes it, and a revoked card is never un-revoked.
            Issue a new one from their profile.
          </div>
        </div>
      </div>

      <div className="box pad0 dt-host">
        <DataTable
          rows={list} rowKey={r => r.id} search="Search archived clients…"
          empty="Nothing archived."
          columns={[
            { label: '', sortable: false, style: { width: 54 }, cell: r => <Avatar client={r} /> },
            {
              label: 'NAME', sortValue: r => r.name_en, onCellClick: r => nav(`/client/${r.id}`),
              cell: r => r.name_en,
            },
            {
              label: 'MOBILE', className: 'mute num', sortValue: r => r.phone || '',
              cell: r => r.phone || '—',
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
              label: 'LAST PLAN', className: 'mute', sortValue: r => r.plan || '',
              cell: r => r.plan || '—',
            },
            {
              label: '', sortable: false, className: 'right',
              cell: r => <button className="sm" onClick={() => unarchive(r)}>Unarchive</button>,
            },
          ]}
        />
      </div>
    </>
  );
}
