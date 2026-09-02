import { Route, Routes } from 'react-router-dom';

import Shell from './components/Shell';
import Dashboard from './views/Dashboard';
import NotBuilt from './views/NotBuilt';

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        {/* Everything else is ported in phase 3; see NotBuilt.jsx. */}
        <Route path="*" element={<NotBuilt />} />
      </Routes>
    </Shell>
  );
}
