import { useMemo, useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/**
 * Search-and-chip multi-select for booking several clients into one session
 * at once. The caller (SessionDetail) checks whether anyone is left to add
 * before opening this — matching app.js's bookIntoSession(), which toasts
 * and never opens the modal when every client is already booked in.
 */
export default function AddStudents({ sessionId, roster, allClients, onSaved }) {
  const { close } = useModal();
  const toast = useToast();
  const inSession = useMemo(() => new Set(roster.map(m => m.id)), [roster]);
  const options = useMemo(() => allClients.filter(c => !inSession.has(c.id)), [allClients, inSession]);

  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(new Map()); // id -> client
  const [saving, setSaving] = useState(false);

  const filtered = options.filter(c => {
    if (selected.has(c.id)) return false;
    const q = query.toLowerCase();
    return (c.name_en || '').toLowerCase().includes(q) || (c.phone || '').toLowerCase().includes(q);
  });

  const select = c => {
    setSelected(m => new Map(m).set(c.id, c));
    setQuery('');
  };
  const remove = id => setSelected(m => { const n = new Map(m); n.delete(id); return n; });

  const save = async () => {
    const ids = Array.from(selected.keys());
    if (!ids.length) return toast('Please select at least one client', 'bad');
    setSaving(true);
    const results = await Promise.allSettled(
      ids.map(id => api(`/sessions/${sessionId}/book`, { method: 'POST', body: { client_id: id } })),
    );
    const failures = results.filter(r => r.status === 'rejected');
    const successes = results.length - failures.length;
    if (!failures.length) {
      toast(`Successfully booked ${successes} student(s)`);
    } else if (successes) {
      toast(`Booked ${successes} student(s), but ${failures.length} failed`, 'bad');
    } else {
      toast(failures[0].reason?.message || 'Failed to book clients', 'bad');
      setSaving(false);
      return;
    }
    close();
    onSaved();
  };

  return (
    <>
      <h3>Add students</h3>
      <div className="mh">Uses one slot from each selected student's plan.</div>

      <div className="chipbox">
        {selected.size === 0
          ? <span className="none">No students selected yet</span>
          : Array.from(selected.values()).map(c => (
            <span key={c.id} className="chip">
              {c.name_en}
              <b onClick={() => remove(c.id)} title="Remove">&times;</b>
            </span>
          ))}
      </div>

      <label>SEARCH CLIENT</label>
      <input
        type="text" placeholder="Type name or phone to search…" value={query}
        onChange={e => setQuery(e.target.value)} style={{ marginBottom: 8 }}
      />

      <div className="picklist">
        {filtered.length
          ? filtered.map(c => (
            <div key={c.id} className="pickrow" onClick={() => select(c)}>
              <span style={{ flex: 1 }}><b>{c.name_en}</b>{c.phone ? ` — ${c.phone}` : ''}</span>
              <span className="pk-meta">{c.remaining ?? 'no plan'} left</span>
            </div>
          ))
          : <div className="dt-none">No matching clients available</div>}
      </div>

      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={saving} onClick={save}>Add Selected</button>
      </div>
    </>
  );
}
