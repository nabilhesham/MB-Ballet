import { useNavigate } from 'react-router-dom';

import { api, useApi } from '../api';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import Avatar from '../components/Avatar';
import Empty from '../components/Empty';

/** Archived instructors, with a way back — see Instructors.jsx's "Archived" button. */
export default function ArchivedInstructors() {
  const { data: list, loading, error, reload } = useApi('/instructors?status=archived');
  const nav = useNavigate();
  const toast = useToast();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const unarchive = async i => {
    await api(`/instructors/${i.id}/unarchive`, { method: 'POST' });
    toast('Instructor restored');
    reload();
  };

  return (
    <>
      <div className="head">
        <div>
          <h1>Archived instructors</h1>
          <div className="sub">{list.length} archived</div>
        </div>
      </div>

      <div className="box pad0 dt-host">
        <DataTable
          rows={list} rowKey={r => r.id} search="Search archived instructors…"
          empty="Nothing archived."
          columns={[
            { label: '', sortable: false, style: { width: 54 }, cell: r => <Avatar client={r} /> },
            {
              label: 'NAME', sortValue: r => r.name, onCellClick: r => nav(`/instructor/${r.id}`),
              cell: r => r.name,
            },
            { label: 'MOBILE', className: 'mute num', sortValue: r => r.phone || '', cell: r => r.phone || '—' },
            { label: 'SPECIALTY', className: 'mute', sortValue: r => r.specialty || '', cell: r => r.specialty || '—' },
            { label: 'RATE', className: 'num mute', sortValue: r => r.hourly_rate, cell: r => `${r.hourly_rate} EGP/h` },
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
