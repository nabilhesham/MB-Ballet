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
      // An enquiry is a person who is not a client yet, so it belongs beside
      // Clients rather than under TEACHING with the timetable.
      { to: '/appointments', icon: '◎', label: 'Appointments' },
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

/* The server's clock against this browser's, and the two timezones with it.
 *
 * `/api/clock` says why this exists; the short version is that `db.now()` on
 * the machine running the app decides when a class is over, when a no-show
 * becomes absent, which day a check-in belongs to and what time it is
 * stamped with -- and nothing in the app could tell a clock an hour fast
 * from a busy evening. The browser is a second clock, and two clocks that
 * disagree are a fact rather than a guess.
 *
 * Re-checked every ten minutes rather than once, because the drift that
 * causes this arrives mid-session: a laptop waking from sleep, or a virtual
 * machine whose clock stops while the host sleeps. */
function useClockCheck() {
  const [state, setState] = useState(null);
  useEffect(() => {
    let live = true;
    const check = async () => {
      try {
        const t0 = Date.now();
        const r = await fetch('/api/clock');
        const t1 = Date.now();
        const s = await r.json();
        if (!live) return;
        setState({
          // The midpoint of the request, not its end: on a slow answer the
          // round trip would otherwise read as the server being behind.
          skew: s.epoch - (t0 + t1) / 2000,
          server: s.offset_minutes,
          browser: -new Date().getTimezoneOffset(),
        });
      } catch { /* the app being unreachable is not a clock problem */ }
    };
    check();
    const id = setInterval(check, 10 * 60 * 1000);
    return () => { live = false; clearInterval(id); };
  }, []);
  return state;
}

const SKEW_TOLERANCE_S = 120;

/* "1h 2m", "3m", "45s" — the size of the disagreement, in the units someone
   would say it in. */
function roughly(seconds) {
  const s = Math.round(Math.abs(seconds));
  if (s < 90) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

const offsetLabel = mins => {
  const sign = mins < 0 ? '-' : '+';
  const a = Math.abs(mins);
  return `${sign}${String(Math.floor(a / 60)).padStart(2, '0')}:${String(a % 60).padStart(2, '0')}`;
};

function ClockWarning() {
  const c = useClockCheck();
  if (!c) return null;
  const bad = Math.abs(c.skew) > SKEW_TOLERANCE_S;
  const tz = c.server !== c.browser;
  if (!bad && !tz) return null;
  return (
    <div className="warnline">
      {bad && (
        <>
          <b>The app&apos;s clock is {roughly(c.skew)} {c.skew > 0 ? 'ahead of' : 'behind'} this
          computer&apos;s.</b>{' '}
          Everything about time is that far out: a class is completed and its
          no-shows marked absent {roughly(c.skew)} {c.skew > 0 ? 'early' : 'late'}, and every
          check-in is stamped {roughly(c.skew)} {c.skew > 0 ? 'late' : 'early'}. Fix the clock on
          the computer running MB Ballet, then reload — nothing in the app can
          correct for it.
        </>
      )}
      {bad && tz && <br />}
      {tz && (
        <>
          <b>The app and this browser disagree about the timezone</b>{' '}
          ({offsetLabel(c.server)} against {offsetLabel(c.browser)}). Day
          boundaries and the times reception reads aloud come from different
          offsets until they match.
        </>
      )}
    </div>
  );
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

      {/* Above the page rather than in the sidebar: a wrong clock makes every
          figure on every screen wrong, which is not a footnote. It clears
          itself the moment the two agree. */}
      <main id="view"><ClockWarning />{children}</main>
    </>
  );
}
