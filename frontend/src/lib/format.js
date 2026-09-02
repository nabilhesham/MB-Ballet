/*
 * Formatting helpers, ported verbatim from static/app.js. Every view needs
 * the same "timestamp -> what a receptionist reads" conversions, so they
 * live in one place rather than being re-derived per component.
 */

export const fmtTime = ts =>
  new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

export const fmtDate = ts =>
  new Date(ts * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' });

export const fmtFull = ts =>
  new Date(ts * 1000).toLocaleString([], {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });

export const fmtDay = ts =>
  new Date(ts * 1000).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });

export const todayISO = () => new Date().toISOString().slice(0, 10);

/* A timestamp as its local calendar day, "YYYY-MM-DD". Not toISOString():
   that is UTC, and an evening class in Alexandria can land on the next day. */
export const isoDay = ts => {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

export const initials = n =>
  (n || '?').split(' ').slice(0, 2).map(w => w[0]).join('').toUpperCase();

export const hrs = h => (h % 1 === 0 ? h : h.toFixed(1)) + (h === 1 ? ' hour' : ' hours');

/* "2026-09" -> "September". Parsed as a local date, not a UTC one: new
   Date('2026-09') is midnight UTC and reads as August in Alexandria. */
export const monthName = m =>
  new Date(+m.slice(0, 4), +m.slice(5, 7) - 1, 1).toLocaleDateString([], { month: 'long' });

export const fmtISO = d =>
  d
    ? new Date(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10))
        .toLocaleDateString([], { day: 'numeric', month: 'short' })
    : '—';
