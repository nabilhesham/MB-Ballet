import { useState } from 'react';

import { api } from '../api';
import { fmtISO } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/**
 * Correct the hours taught on one day — an instructor who stayed for an
 * extra rehearsal taught it whether or not a session row says so.
 *
 * One day only, and one total, matching how it is shown. Underneath it
 * writes a dated correction rather than editing a session's duration or a
 * salary-sheet row, so the timetable and the sheet still say what they
 * always said, and the corrections accumulate into a daily history that any
 * wider range picks up by summing (see access.adjust_taught_hours).
 */
export default function EditInstructorHours({ instructorId, day, currentTotal, scheduled, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [total, setTotal] = useState(currentTotal);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (total === '' || Number.isNaN(Number(total))) return toast('Enter the hours', 'bad');
    if (Number(total) < 0) return toast('Hours cannot be negative', 'bad');
    setSaving(true);
    try {
      await api(`/instructors/${instructorId}/hours-adjustment`, { method: 'POST', body: {
        day, new_total: Number(total), note: note.trim() || null,
      } });
      close();
      toast('Hours updated');
      return onSaved();
    } catch (e) { setSaving(false); return toast(e.message, 'bad'); }
  };

  const delta = Number(total) - Number(scheduled ?? 0);

  return (
    <>
      <h3>Hours taught on {fmtISO(day)}</h3>
      <div className="mh">
        The timetable has <b>{scheduled ?? 0} h</b> of completed sessions for this day.
        Save a different total and the difference is recorded as a dated correction —
        nothing on the timetable or the salary sheet is rewritten.
      </div>
      <label>TOTAL HOURS TAUGHT</label>
      <input type="number" step="0.25" min="0" value={total}
             onChange={e => setTotal(e.target.value)} />
      {Number.isFinite(delta) && delta !== 0 && (
        <div className="hint">
          Recorded as {delta > 0 ? '+' : ''}{Math.round(delta * 100) / 100} h against this day.
        </div>
      )}
      <label>NOTE (OPTIONAL)</label>
      <input value={note} onChange={e => setNote(e.target.value)}
             placeholder="e.g. covered an extra rehearsal" />
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={saving} onClick={save}>Save</button>
      </div>
    </>
  );
}
