import { useEffect, useState } from 'react';

import { api } from '../api';
import { fmtDate, fmtTime } from '../lib/format';
import { useModal } from '../components/Modal';
import DataTable from '../components/DataTable';
import { Pill, StatusPill } from '../components/Pill';
import Empty from '../components/Empty';

/** Read-only popup from a payment-history row: every session that plan covered. */
export default function PlanSessionsPopup({ clientId, planId, name }) {
  const { close } = useModal();
  const [list, setList] = useState(null);

  useEffect(() => {
    api(`/clients/${clientId}/plan/${planId}/sessions`).then(setList);
  }, [clientId, planId]);

  if (list === null) return <Empty>Loading…</Empty>;

  const present = list.filter(x => x.status === 'present').length;
  const absent = list.filter(x => x.status === 'absent').length;

  return (
    <>
      <h3>{name}</h3>
      <div className="mh">Every session this payment covered.</div>
      <div className="row" style={{ margin: '14px 0' }}>
        <Pill kind="ok">{present} attended</Pill>
        <Pill kind="bad">{absent} absent</Pill>
        <Pill kind="info">{list.length - present - absent} upcoming</Pill>
      </div>
      <div className="box pad0" style={{ maxHeight: '50vh', overflowY: 'auto' }}>
        <DataTable
          rows={list} rowKey={r => r.session_id} empty="No sessions assigned to this plan."
          columns={[
            {
              label: 'WHEN', className: 'num mute', sortValue: r => r.starts_at,
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
      <div className="acts"><button className="pri" onClick={close}>Close</button></div>
    </>
  );
}
