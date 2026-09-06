import { fmtFull, isoDay } from '../lib/format';
import DataTable from '../components/DataTable';

/**
 * Pick one session out of every class, with the same search box the sessions
 * view has. Shared by the three places on the client profile that correct a
 * booking after the fact — move an upcoming session, add one to a plan, and
 * name the session someone was actually present at.
 *
 * Deliberately not used by the plan pickers: selling a plan stays locked to
 * its own class (see SessionPickList and access.add_plan).
 *
 * DataTable searches the row's own scalar fields, and starts_at is a raw
 * unix number, so a typed date would never match — `_find` carries the
 * readable date and the class name for it to hit. Same trick the instructor
 * page's session tables use.
 */
export default function SessionPickTable({ sessions, chosenId, onPick, emptyText }) {
  const rows = sessions.map(s => ({
    ...s,
    _find: `${isoDay(s.starts_at)} ${fmtFull(s.starts_at)} ${s.class_name || ''} `
      + `${s.instructor_name || ''}`,
  }));

  return (
    <div className="box pad0 dt-host" style={{ maxHeight: '46vh', overflowY: 'auto' }}>
      <DataTable
        rows={rows} rowKey={r => r.id}
        search="Search by class, instructor or date…"
        empty={emptyText || 'No sessions to choose from.'}
        onRowClick={r => onPick(r.id)}
        columns={[
          {
            label: 'WHEN', sortValue: r => r.starts_at,
            cell: r => (
              <>
                <input
                  type="radio" checked={chosenId === r.id} readOnly
                  style={{ width: 'auto', marginRight: 9, verticalAlign: 'middle' }}
                />
                {fmtFull(r.starts_at)}
              </>
            ),
          },
          {
            label: 'CLASS', sortValue: r => r.class_name || '',
            cell: r => (
              <><span className="dot" style={{ background: r.colour }} />{r.class_name}</>
            ),
          },
          {
            label: 'INSTRUCTOR', className: 'mute', hideSm: true,
            sortValue: r => r.instructor_name || '',
            cell: r => r.instructor_name || 'no instructor',
          },
          {
            label: 'BOOKED', className: 'num mute', sortValue: r => r.booked ?? 0,
            cell: r => (r.booked == null ? '—' : r.booked),
          },
        ]}
      />
    </div>
  );
}
