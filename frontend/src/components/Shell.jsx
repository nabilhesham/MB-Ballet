/*
 * The sidebar, topbar, off-canvas drawer and clock — replaces the markup
 * that used to be hardcoded in static/index.html plus the router's
 * active-link/page-title/drawer-close logic from static/app.js's render().
 */
import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

const NAV = [
  {
    group: 'OVERVIEW',
    items: [
      { to: '/', icon: '◈', label: 'Dashboard' },
      { to: '/calendar', icon: '▦', label: 'Calendar' },
    ],
  },
  {
    group: 'TEACHING',
    items: [
      { to: '/classes', icon: '◇', label: 'Classes' },
      { to: '/sessions', icon: '▤', label: 'Sessions' },
      { to: '/instructors', icon: '◐', label: 'Instructors' },
    ],
  },
  {
    group: 'PEOPLE',
    items: [
      { to: '/clients', icon: '◉', label: 'Clients' },
      { to: '/cards', icon: '▢', label: 'Cards & renewals' },
    ],
  },
];

// Mirrors static/app.js's render(): a detail route's path has its trailing
// numeric id stripped before matching, so a nav item is "on" for an exact
// match or a path that starts with its route (excluding "/", which would
// otherwise match every route).
function isActive(pathname, to) {
  const path = pathname.replace(/\/\d+$/, '');
  return path === to || (to !== '/' && path.startsWith(to));
}

function useClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now.toLocaleString([], {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

const closeNav = () => document.body.classList.remove('nav-open');

export default function Shell({ children }) {
  const { pathname } = useLocation();
  const clock = useClock();

  const active = NAV.flatMap(g => g.items).find(i => isActive(pathname, i.to));
  const title = active ? active.label : 'MB Ballet';

  useEffect(() => {
    document.title = active ? `${title} — MB Ballet Academy` : 'MB Ballet Academy';
  }, [title, active]);

  // A route change closes the drawer, same as the old per-link click handler.
  useEffect(() => { closeNav(); }, [pathname]);

  useEffect(() => {
    const onEsc = e => { if (e.key === 'Escape') closeNav(); };
    document.addEventListener('keydown', onEsc);
    return () => document.removeEventListener('keydown', onEsc);
  }, []);

  return (
    <>
      <div className="topbar">
        <button
          className="burger ghost" aria-label="Menu"
          onClick={() => document.body.classList.toggle('nav-open')}
        >☰</button>
        <img src="/static/logo-mark.png" alt=""
             style={{ width: 26, height: 26, objectFit: 'contain' }} />
        <div className="tname">{title}</div>
        <a className="btn sm" href="/reception" target="_blank" rel="noreferrer">Reception</a>
      </div>
      <div className="scrim" onClick={closeNav} />

      <nav>
        <div className="brand">
          <img src="/static/logo-mark.png" alt="" />
          <div><span className="mark">ACADEMY</span><span className="name">MB Ballet</span></div>
        </div>

        {NAV.map(g => (
          <div key={g.group}>
            <div className="grp">{g.group}</div>
            {g.items.map(i => (
              <Link key={i.to} to={i.to} className={isActive(pathname, i.to) ? 'on' : undefined}>
                <span className="ic">{i.icon}</span> {i.label}
              </Link>
            ))}
          </div>
        ))}

        <div className="spacer" />
        <a href="/reception" target="_blank" rel="noreferrer">
          <span className="ic">▶</span> Open reception
        </a>
        <div className="foot">{clock}</div>
      </nav>

      <main id="view">{children}</main>

      <div className="veil" id="veil"><div className="modal" id="modal" /></div>
    </>
  );
}
