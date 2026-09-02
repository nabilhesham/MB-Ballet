/*
 * Replaces static/app.js's confirmBox()/runConfirm(): closes the modal
 * immediately (not after the action finishes — matching the original, so an
 * error toast can still appear after the modal is already gone) and runs the
 * action, catching a rejection into a toast.
 */
import { useModal } from './Modal';
import { useToast } from './Toast';

export function useConfirm() {
  const { open, close } = useModal();
  const toast = useToast();

  return ({ title, message, label, danger = true, onConfirm }) => {
    const run = async () => {
      close();
      try { await onConfirm(); }
      catch (e) { toast(e.message, 'bad'); }
    };
    open(
      <>
        <h3>{title}</h3>
        <div className="mh" style={{ margin: '8px 0 0', lineHeight: 1.7 }}>{message}</div>
        <div className="acts">
          <button onClick={close}>Cancel</button>
          <button className={danger ? 'danger' : 'pri'} onClick={run}>{label}</button>
        </div>
      </>,
    );
  };
}
