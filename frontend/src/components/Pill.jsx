import { fmtISO, todayISO } from '../lib/format';

export function Pill({ kind, children }) {
  return <span className={'pill ' + kind}>{children}</span>;
}

/** Sessions-left pill, used in several tables. */
export function BalancePill({ row }) {
  if (row.remaining === null || row.remaining === undefined)
    return <Pill kind="grey">no plan</Pill>;
  if (row.expires_on && row.expires_on < todayISO())
    return <Pill kind="bad">expired</Pill>;
  if (row.remaining <= 0) return <Pill kind="bad">0 left</Pill>;
  if (row.remaining <= 2) return <Pill kind="warn">{row.remaining} left</Pill>;
  return <Pill kind="ok">{row.remaining} left</Pill>;
}

export function StatusPill({ status }) {
  if (status === 'present') return <Pill kind="ok">present</Pill>;
  if (status === 'absent') return <Pill kind="bad">absent</Pill>;
  return <Pill kind="info">booked</Pill>;
}

/**
 * Whether a plan has been paid for, and when. One component so the profile,
 * the payment history and the plan popup can never disagree.
 *
 * Unpaid is `warn`, not `bad`: it is something for reception to chase, not a
 * fault in the record — the same reading "no mobile number" gets.
 */
export function PaidPill({ paidOn }) {
  if (!paidOn) return <Pill kind="warn">unpaid</Pill>;
  return <Pill kind="ok">paid {fmtISO(paidOn)}</Pill>;
}
