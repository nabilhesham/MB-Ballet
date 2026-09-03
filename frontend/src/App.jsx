import { Route, Routes } from 'react-router-dom';

import Shell from './components/Shell';
import Dashboard from './views/Dashboard';
import Classes from './views/Classes';
import ClassDetail from './views/ClassDetail';
import Instructors from './views/Instructors';
import InstructorDetail from './views/InstructorDetail';
import ArchivedInstructors from './views/ArchivedInstructors';
import Cards from './views/Cards';
import Sessions from './views/Sessions';
import SessionDetail from './views/SessionDetail';
import Clients from './views/Clients';
import ClientDetail from './views/ClientDetail';
import Calendar from './views/Calendar';
import NotBuilt from './views/NotBuilt';

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/classes" element={<Classes />} />
        <Route path="/class/:id" element={<ClassDetail />} />
        <Route path="/instructors" element={<Instructors />} />
        <Route path="/instructors/archived" element={<ArchivedInstructors />} />
        <Route path="/instructor/:id" element={<InstructorDetail />} />
        <Route path="/cards" element={<Cards />} />
        <Route path="/sessions" element={<Sessions />} />
        <Route path="/session/:id" element={<SessionDetail />} />
        <Route path="/clients" element={<Clients />} />
        <Route path="/client/:id" element={<ClientDetail />} />
        <Route path="/calendar" element={<Calendar />} />
        {/* Everything else is ported in phase 3; see NotBuilt.jsx. */}
        <Route path="*" element={<NotBuilt />} />
      </Routes>
    </Shell>
  );
}
