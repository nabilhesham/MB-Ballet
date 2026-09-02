import { useModal } from '../components/Modal';

/** Shown instead of a plain confirm when the session has recorded attendance. */
export default function DeleteSessionWithAttendance({ className, markedCount, onCancelInstead, onForceDelete }) {
  const { close } = useModal();
  return (
    <>
      <h3>Delete a session with attendance</h3>
      <div className="mh">
        <b>{className}</b> has {markedCount} recorded attendance {markedCount === 1 ? 'record' : 'records'}.
      </div>
      <div className="warnline" style={{ marginTop: 16 }}>
        Deleting erases who attended. Every slot returns to the clients, but the
        record that the class ran is lost.
      </div>
      <div className="infoline">
        <b>Cancelling</b> returns the slots too and keeps the history. That is
        usually what you want.
      </div>
      <div className="acts">
        <button onClick={close}>Back</button>
        <button onClick={() => { close(); onCancelInstead(); }}>Cancel instead</button>
        <button className="danger" onClick={() => { close(); onForceDelete(); }}>Delete permanently</button>
      </div>
    </>
  );
}
