/*
 * Thin fetch wrapper, ported from static/app.js's api(), plus a small hook
 * that replaces render()'s "Loading…" / "Could not load" states.
 *
 * No cache layer on top of this on purpose (see the plan's D5): one user, one
 * laptop, one SQLite file behind it — "mutate, then reload()" is correct and
 * instant, so there is no invalidation problem worth a state library for.
 */
import { useCallback, useEffect, useState } from 'react';

export async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || 'request failed');
  return data;
}

/**
 * Fetch `path` on mount and whenever it changes. Returns
 * {data, loading, error, reload} — call reload() after a mutation instead of
 * keeping a second copy of the state around.
 *
 * **`path` must be stable across renders.** The effect is keyed on it, so a
 * path rebuilt from anything that moves on its own — `Date.now()` evaluated
 * in the component body is the one that bit — means "whenever it changes"
 * becomes "on every render", and the page fetches itself forever.
 *
 * The loop is silent, which is what makes it worth this comment: every run
 * sets loading=true, so a caller with the usual `if (loading) return
 * <Empty>Loading…</Empty>` never draws the rows it just fetched. It is also
 * invisible on a fast backend, because the refetch only triggers once the
 * value has actually moved — a request answered in milliseconds returns
 * before the clock ticks over and the loop stops after one pass. Against a
 * networked backend, where nothing is answered inside a second, it never
 * stops.
 *
 * Derive such a value once with `useState(() => …)` and pass that. Views
 * that take a period from the user (Dashboard, InstructorDetail) already do,
 * and the modals keep `Date.now()` inside a mount-only effect instead.
 */
export function useApi(path) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api(path)
      .then(d => { if (!cancelled) setData(d); })
      .catch(e => { if (!cancelled) setError(e); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [path, tick]);

  return { data, loading, error, reload };
}
