import { useNavigate } from 'react-router-dom';

import { api, useApi } from '../api';
import { hrs } from '../lib/format';
import { useModal } from '../components/Modal';
import { Pill } from '../components/Pill';
import Empty from '../components/Empty';
import ClassForm from '../modals/ClassForm';

export default function Classes() {
  const { data: classes, loading, error, reload } = useApi('/classes');
  const { open } = useModal();
  const nav = useNavigate();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const openNewClass = async () => {
    const instructors = await api('/instructors');
    open(<ClassForm instructors={instructors} onSaved={reload} />);
  };

  return (
    <>
      <div className="head">
        <div><h1>Classes</h1><div className="sub">{classes.length} active</div></div>
        <div className="row">
          <button onClick={() => nav('/classes/archived')}>Archived</button>
          <button className="pri" onClick={openNewClass}>New class</button>
        </div>
      </div>

      {classes.length ? (
        <div className="grid g3">
          {classes.map(c => (
            <div
              key={c.id} className="box tap" style={{ borderLeft: `3px solid ${c.colour}` }}
              onClick={() => nav(`/class/${c.id}`)}
            >
              <div style={{ fontSize: 16, fontWeight: 600 }}>{c.name}</div>
              <div className="row" style={{ marginTop: 14 }}>
                <Pill kind="info">{c.students} students</Pill>
                <Pill kind="grey">{hrs(c.duration_hours)}</Pill>
                {c.level && <Pill kind="grey">{c.level}</Pill>}
              </div>
              <div className="sub" style={{ marginTop: 12 }}>
                {c.upcoming} upcoming session{c.upcoming === 1 ? '' : 's'}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="box"><Empty title="No classes yet">Create one to start scheduling.</Empty></div>
      )}
    </>
  );
}
