import { useNavigate } from 'react-router-dom';

import { useApi } from '../api';
import { initials } from '../lib/format';
import { useModal } from '../components/Modal';
import { Pill } from '../components/Pill';
import Empty from '../components/Empty';
import InstructorForm from '../modals/InstructorForm';

export default function Instructors() {
  const { data: list, loading, error, reload } = useApi('/instructors');
  const { open } = useModal();
  const nav = useNavigate();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  return (
    <>
      <div className="head">
        <div><h1>Instructors</h1><div className="sub">{list.length} on the team</div></div>
        <button className="pri" onClick={() => open(<InstructorForm onSaved={reload} />)}>
          New instructor
        </button>
      </div>

      {list.length ? (
        <div className="grid g3">
          {list.map(i => (
            <div key={i.id} className="box tap" onClick={() => nav(`/instructor/${i.id}`)}>
              <div className="row" style={{ gap: 13 }}>
                <span className="avatar">{initials(i.name)}</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }}>{i.name}</div>
                  <div className="sub">{i.specialty || '—'}</div>
                </div>
              </div>
              <div className="row" style={{ marginTop: 14 }}>
                <Pill kind="info">{i.sessions_taught} sessions</Pill>
                <Pill kind="grey">{i.hours_taught} h</Pill>
                <Pill kind="brand">{i.hourly_rate} EGP/h</Pill>
              </div>
              <div className="sub" style={{ marginTop: 12 }}>Earned {i.earned.toLocaleString()} EGP</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="box"><Empty title="No instructors yet">Add one before scheduling sessions.</Empty></div>
      )}
    </>
  );
}
