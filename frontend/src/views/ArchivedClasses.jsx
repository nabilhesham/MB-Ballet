import { useNavigate } from 'react-router-dom';

import { api, useApi } from '../api';
import { hrs } from '../lib/format';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import Empty from '../components/Empty';

/** Archived classes, with a way back — see Classes.jsx's "Archived" button. */
export default function ArchivedClasses() {
  const { data: list, loading, error, reload } = useApi('/classes?status=archived');
  const nav = useNavigate();
  const toast = useToast();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const unarchive = async c => {
    await api(`/classes/${c.id}/unarchive`, { method: 'POST' });
    toast('Class restored');
    reload();
  };

  return (
    <>
      <div className="head">
        <div>
          <h1>Archived classes</h1>
          <div className="sub">
            {list.length} archived — restoring brings the class back, but not the upcoming
            sessions the archive released; those get scheduled fresh.
          </div>
        </div>
      </div>

      <div className="box pad0 dt-host">
        <DataTable
          rows={list} rowKey={r => r.id} search="Search archived classes…"
          empty="Nothing archived."
          columns={[
            {
              label: 'NAME', sortValue: r => r.name, onCellClick: r => nav(`/class/${r.id}`),
              cell: r => <><span className="dot" style={{ background: r.colour }} />{r.name}</>,
            },
            { label: 'LEVEL', className: 'mute', sortValue: r => r.level || '', cell: r => r.level || '—' },
            {
              label: 'DURATION', className: 'mute num', hideSm: true, sortValue: r => r.duration_hours,
              cell: r => hrs(r.duration_hours),
            },
            { label: 'STUDENTS', className: 'num', sortValue: r => r.students, cell: r => r.students },
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
