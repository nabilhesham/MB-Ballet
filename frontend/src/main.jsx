import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';

import App from './App.jsx';
import { ToastProvider } from './components/Toast.jsx';
import { ModalProvider } from './components/Modal.jsx';

// HashRouter, not BrowserRouter: preserves the #/clients, #/client/17 URLs
// static/app.js already uses, byte for byte, and needs no server-side
// route handling — every bookmark from the old app keeps working.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <HashRouter>
      <ToastProvider>
        <ModalProvider>
          <App />
        </ModalProvider>
      </ToastProvider>
    </HashRouter>
  </StrictMode>,
);
