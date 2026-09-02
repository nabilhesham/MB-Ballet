import { Link, useNavigate } from 'react-router-dom';

import { useApi } from '../api';
import { fmtTime, monthName } from '../lib/format';
import DataTable from '../components/DataTable';
import { Pill, BalancePill } from '../components/Pill';
import Empty from '../components/Empty';

export default function Dashboard() {
  const { data: d, loading, error } = useApi('/dashboard');
  const nav = useNavigate();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const s = d.stats;
  const now = Date.now() / 1000;

  return (
    <>
      <div className="head">
        <div>
          <h1>Dashboard</h1>
          <div className="sub">
            {new Date().toLocaleDateString([], {
              weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
            })}
          </div>
        </div>
        <div className="row">
          <a className="btn" href="/reception" target="_blank" rel="noreferrer">
            Open reception screen
          </a>
          <Link className="btn pri" to="/clients">New client</Link>
        </div>
      </div>

      <div className="grid g4">
        <div className="box kpi">
          <div className="k">ARRIVED</div>
          <div className="v" style={{ color: 'var(--ok)' }}>{s.exp_arrived}</div>
          <div className="n">of {s.exp_expected} expected today</div>
        </div>
        <div className="box kpi">
          <div className="k">STILL TO ARRIVE</div>
          <div className="v" style={{ color: s.exp_still_due ? 'var(--warn)' : 'var(--ink)' }}>
            {s.exp_still_due}
          </div>
          <div className="n">{s.exp_absent ? `${s.exp_absent} marked absent` : 'none absent'}</div>
        </div>
        <div className="box kpi">
          <div className="k">SESSIONS TODAY</div>
          <div className="v">{d.today_sessions.length}</div>
          <div className="n">{s.sessions_week} this week</div>
        </div>
        <div className="box kpi">
          <div className="k">NEED ATTENTION</div>
          <div className="v" style={{ color: d.attention.length ? 'var(--warn)' : 'var(--ok)' }}>
            {d.attention.length}
          </div>
          <div className="n">low, expiring or unassigned</div>
        </div>
      </div>

      <div className="grid g2" style={{ marginTop: 15 }}>
        <div className="box kpi">
          <div className="k">NEW CLIENTS IN {monthName(s.mo_month).toUpperCase()}</div>
          <div className="v" style={{ color: 'var(--brand)' }}>{s.mo_new_clients}</div>
          <div className="n">
            {s.mo_new_clients_prev} the month before
            {s.mo_new_plans ? ` · ${s.mo_new_plans} plan${s.mo_new_plans === 1 ? '' : 's'} bought` : ''}
          </div>
        </div>
        <div className="box kpi">
          <div className="k">EARNED FROM THEM</div>
          <div className="v" style={{ fontSize: 22, paddingTop: 6, color: 'var(--brand-deep)' }}>
            {s.mo_new_revenue.toLocaleString()}{' '}
            <span style={{ fontSize: 12, color: 'var(--mute)' }}>EGP</span>
          </div>
          <div className="n">
            {s.mo_revenue.toLocaleString()} from every plan sold this month
            {s.mo_unpriced
              ? <> · <span style={{ color: 'var(--warn)' }}>{s.mo_unpriced} with no price on the sheet</span></>
              : ''}
          </div>
        </div>
      </div>

      <h2>Today's schedule</h2>
      <div className="box pad0 dt-host">
        <DataTable
          rows={d.today_sessions}
          rowKey={r => r.id}
          onRowClick={r => nav(`/session/${r.id}`)}
          empty="No sessions scheduled today."
          columns={[
            {
              label: 'TIME', className: 'num mute', sortValue: r => r.starts_at,
              cell: r => fmtTime(r.starts_at),
            },
            {
              label: 'CLASS', sortValue: r => r.class_name,
              cell: r => {
                const live = r.starts_at <= now && r.starts_at + r.duration_hours * 3600 > now;
                return (
                  <>
                    <span className="dot" style={{ background: r.colour }} />
                    {r.class_name}{' '}
                    {r.status !== 'scheduled'
                      ? <Pill kind="grey">{r.status}</Pill>
                      : (live ? <Pill kind="ok">now</Pill> : null)}
                  </>
                );
              },
            },
            {
              label: 'INSTRUCTOR', className: 'mute', hideSm: true,
              sortValue: r => r.instructor_name || '',
              cell: r => r.instructor_name || '—',
            },
            {
              label: 'ATTENDED', className: 'num', sortValue: r => (r.booked ? r.attended / r.booked : 0),
              cell: r => {
                const pct = r.booked ? Math.round((100 * r.attended) / r.booked) : 0;
                return (
                  <>
                    {r.attended}/{r.booked}
                    <div className="bar"><i style={{ width: `${pct}%`, background: r.colour }} /></div>
                  </>
                );
              },
            },
          ]}
        />
      </div>

      <div className="grid g2" style={{ marginTop: 24 }}>
        <div>
          <h2>Needs attention</h2>
          <div className="box pad0 dt-host">
            <DataTable
              rows={d.attention}
              rowKey={r => r.id}
              onRowClick={r => nav(`/client/${r.id}`)}
              empty="Everyone is in good standing."
              columns={[
                { label: 'CLIENT', sortValue: r => r.name_en, cell: r => r.name_en },
                {
                  label: 'MOBILE', className: 'mute num', sortValue: r => r.phone || '',
                  cell: r => r.phone || '—',
                },
                {
                  label: 'STATUS', sortValue: r => (r.unassigned > 0 ? -1 : (r.remaining ?? 999)),
                  cell: r => (r.unassigned > 0
                    ? <Pill kind="warn">{r.unassigned} unassigned</Pill>
                    : <BalancePill row={r} />),
                },
              ]}
            />
          </div>
        </div>
        <div>
          <h2>Recent activity</h2>
          <div className="box pad0 dt-host">
            <DataTable
              rows={d.recent.slice(0, 12)}
              rowKey={r => r.id}
              empty="No scans yet today."
              columns={[
                {
                  label: 'TIME', className: 'num mute', sortValue: r => r.scanned_at,
                  cell: r => fmtTime(r.scanned_at),
                },
                {
                  label: 'CLIENT', sortValue: r => r.name_en || '',
                  cell: r => (
                    <>
                      {r.name_en || '—'}
                      <div className="sub">{r.class_name || r.reason || ''}</div>
                    </>
                  ),
                },
                {
                  label: 'RESULT',
                  sortValue: r => (r.confirmed_at ? 2 : (r.decision === 'allow' ? 1 : 0)),
                  cell: r => (r.confirmed_at
                    ? <Pill kind="ok">in</Pill>
                    : (r.decision === 'allow'
                      ? <Pill kind="grey">not confirmed</Pill>
                      : <Pill kind="bad">denied</Pill>)),
                },
              ]}
            />
          </div>
        </div>
      </div>
    </>
  );
}
