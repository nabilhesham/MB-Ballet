/*
 * Replaces static/app.js's openModal()/closeModal(): one veil+modal pair,
 * shared by the whole app. Same behaviour — Escape closes it, clicking the
 * veil (not the modal itself) closes it, the first field autofocuses — just
 * driven by React state instead of innerHTML.
 */
import { createContext, useContext, useEffect, useRef, useState } from 'react';

const ModalCtx = createContext({ open: () => {}, close: () => {} });

export function ModalProvider({ children }) {
  const [content, setContent] = useState(null);
  const [wide, setWide] = useState(false);
  const bodyRef = useRef(null);

  const close = () => setContent(null);
  const open = (node, opts = {}) => {
    setContent(node);
    setWide(!!opts.wide);
  };

  useEffect(() => {
    if (!content) return undefined;
    const onEsc = e => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onEsc);
    return () => document.removeEventListener('keydown', onEsc);
  }, [content]);

  useEffect(() => {
    if (!content || !bodyRef.current) return;
    const first = bodyRef.current.querySelector('input,select,textarea');
    if (first) first.focus();
  }, [content]);

  return (
    <ModalCtx.Provider value={{ open, close }}>
      {children}
      <div
        className={'veil' + (content ? ' on' : '')}
        onClick={e => { if (e.target === e.currentTarget) close(); }}
      >
        <div className={'modal' + (wide ? ' wide' : '')} ref={bodyRef}>
          {content}
        </div>
      </div>
    </ModalCtx.Provider>
  );
}

export function useModal() {
  return useContext(ModalCtx);
}
