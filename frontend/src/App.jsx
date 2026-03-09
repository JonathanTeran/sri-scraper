import { Routes, Route, NavLink, useLocation } from 'react-router-dom';
import { LayoutDashboard, Users, Activity, FileText, Settings, User } from 'lucide-react';
import './App.css';

import Overview from './pages/Overview';
import Tenants from './pages/Tenants';

import Executions from './pages/Executions';
import Documents from './pages/Documents';
function App() {
  const location = useLocation();

  const navItems = [
    { path: '/', label: 'Global', icon: <LayoutDashboard size={20} /> },
    { path: '/usuarios', label: 'Usuarios', icon: <Users size={20} /> },
    { path: '/procesos', label: 'Procesos', icon: <Activity size={20} /> },
    { path: '/consumos', label: 'Consumos', icon: <FileText size={20} /> },
  ];

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="sidebar-logo">
            SRI
          </div>
          <span className="sidebar-title">Admin Scraper</span>
        </div>

        <nav className="nav-links">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        <header className="top-header">
          <div className="user-profile">
            <span style={{ fontSize: '0.875rem', fontWeight: 500, color: 'var(--text-secondary)' }}>Administrador</span>
            <div className="avatar">
              <User size={18} />
            </div>
            <Settings size={18} style={{ color: 'var(--text-tertiary)', marginLeft: 'var(--space-2)' }} />
          </div>
        </header>

        {/* Routes */}
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/usuarios" element={<Tenants />} />
          <Route path="/procesos" element={<Executions />} />
          <Route path="/consumos" element={<Documents />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
