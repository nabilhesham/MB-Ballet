/*
 * Ported from static/app.js's toast(): a single message shown for 2.8s,
 * classes 'toast on ok'/'toast on bad', reset to plain 'toast' after. One
 * instance for the whole app, mounted once in main.jsx.
 */
import { createContext, useCallback, useContext, useRef, useState } from 'react';

const ToastCtx = createContext(() => {});

export function ToastProvider({ children }) {
  const [state, setState] = useState({ msg: '', kind: 'ok', on: false });
  const timer = useRef();

  const show = useCallback((msg, kind = 'ok') => {
    clearTimeout(timer.current);
    setState({ msg, kind, on: true });
    timer.current = setTimeout(() => setState(s => ({ ...s, on: false })), 2800);
  }, []);

  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div className={'toast' + (state.on ? ' on ' + state.kind : '')}>{state.msg}</div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}
