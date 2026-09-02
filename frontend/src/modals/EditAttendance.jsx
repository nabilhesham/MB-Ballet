import { api } from '../api';
import { fmtFull } from '../lib/format';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import { StatusPill } from '../components/Pill';

/** Correct a past attendance record from a client's history table. */
export default function EditAttendance({ clientId, sessionId, className, status, ts, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const fix = async newStatus => {
    try {
      await api(`/sessions/${sessionId}/status-of`, { method: 'POST', body: { client_id: clientId, status: newStatus } });
      close();
      toast(newStatus === 'present' ? 'Marked present' : 'Marked absent');
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>{className}</h3>
      <div className="mh">{fmtFull(ts)}</div>
      <div className="row" style={{ margin: '18px 0' }}>Currently <StatusPill status={status} /></div>
      <div className="sub">
        Both present and absent use the client's slot. Changing this only
        corrects the record of what happened.
      </div>
      <div className="acts">
        <button onClick={close}>Cancel</button>
        <button className={status === 'absent' ? 'pri' : ''} onClick={() => fix('absent')}>Mark absent</button>
        <button className={status === 'present' ? 'pri' : ''} onClick={() => fix('present')}>Mark present</button>
      </div>
    </>
  );
}
