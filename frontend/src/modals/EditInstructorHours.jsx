import { useState } from 'react';

import { api } from '../api';
import { fmtISO } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/**
 * Correct the hours logged for the period currently being viewed. A single
 * total, matching how it's shown — under the hood this writes one new dated
 * correction rather than rewriting anything the salary sheet imported, so
 * what the sheet actually said stays visible (see access.adjust_logged_hours).
 */
export default function EditInstructorHours({ instructorId, from, to, currentTotal, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [total, setTotal] = useState(currentTotal);
  const [note, setNote] = useState('');

  const save = async () => {
    try {
      await api(`/instructors/${instructorId}/hours-adjustment`, { method: 'POST', body: {
        from, to, new_total: Number(total), note: note.trim() || null,
      } });
      close();
      toast('Hours updated');
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>Edit hours for this period</h3>
      <div className="mh">
        {fmtISO(from)} to {fmtISO(to)}. Recorded as a correction, not a rewrite — the salary
        sheet's own rows stay exactly as imported.
      </div>
      <label>TOTAL HOURS</label>
      <input type="number" step="0.25" value={total} onChange={e => setTotal(e.target.value)} />
      <label>NOTE (OPTIONAL)</label>
      <input value={note} onChange={e => setNote(e.target.value)} placeholder="e.g. covered an extra rehearsal" />
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>Save</button>
      </div>
    </>
  );
}
