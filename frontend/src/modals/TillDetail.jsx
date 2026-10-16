import { useApi } from '../api';
import { useModal } from '../components/Modal';
import { fmtISODay } from '../lib/format';

/**
 * What is behind TAKEN TODAY: every plan paid on the day and every product
 * sold on it, named.
 *
 * **A drill-down, fetched when it opens.** The card is two numbers; the
 * list behind it is two queries nobody needs until somebody asks "who paid
 * that?" — and the dashboard is the page reception opens most, which is
 * the same reasoning that moved the attention list off it. See
 * /api/dashboard/till.
 *
 * Both halves are listed separately rather than merged into one table.
 * They are different kinds of money with different columns — a plan names
 * a class and a sale names a quantity — and the question reception is
 * answering is usually about one or the other.
 */
export default function TillDetail({ on }) {
  const { close } = useModal();
  const { data, loading, error } = useApi(`/dashboard/till?on=${on}`);

  const row = (left, right) => (
    <div
      className="row"
      style={{ justifyContent: 'space-between', padding: '7px 0',
               borderBottom: '1px solid var(--line)' }}
    >
      {left}{right}
    </div>
  );

  return (
    <>
      <h3>Taken on {fmtISODay(on)}</h3>
      {loading && <div className="mh">Loading…</div>}
      {error && <div className="mh">Could not load: {error.message}</div>}
      {data && (
        <>
          <div className="mh">
            {data.paid.toLocaleString()} EGP in total —{' '}
            {data.plan_paid.toLocaleString()} from plans and{' '}
            {data.shop_paid.toLocaleString()} from the shop.
            {(data.plan_unpriced || data.shop_unpriced)
              ? ` ${data.plan_unpriced + data.shop_unpriced} with no amount written down, counted but not added.`
              : ''}
          </div>

          <h4 style={{ margin: '16px 0 2px' }}>
            Plans · {data.plans.length}
            <span className="sub" style={{ float: 'right', fontWeight: 400 }}>
              {data.plan_paid.toLocaleString()} EGP
            </span>
          </h4>
          {data.plans.length ? data.plans.map(p => row(
            <span key="l">
              <a href={`#/client/${p.client_id}`}>{p.client_name}</a>
              <span className="sub" style={{ display: 'block' }}>
                {p.colour && <span className="dot" style={{ background: p.colour }} />}
                {p.class_name || 'no class'} · {p.plan}
              </span>
            </span>,
            <span key="r" className="num" style={{ fontFamily: 'var(--mono)' }}>
              {p.price == null ? 'no amount' : `${p.price.toLocaleString()} EGP`}
            </span>,
          )) : <div className="empty">No plan was paid for on this day.</div>}

          <h4 style={{ margin: '18px 0 2px' }}>
            Products · {data.items} item{data.items === 1 ? '' : 's'}
            <span className="sub" style={{ float: 'right', fontWeight: 400 }}>
              {data.shop_paid.toLocaleString()} EGP
            </span>
          </h4>
          {data.products.length ? data.products.map(s => row(
            <span key="l">
              <a href={`#/client/${s.client_id}`}>{s.client_name}</a>
              <span className="sub" style={{ display: 'block' }}>
                {s.qty} × {s.product_name}
              </span>
            </span>,
            <span key="r" className="num" style={{ fontFamily: 'var(--mono)' }}>
              {s.unit_price == null
                ? 'no price'
                : `${((s.qty || 0) * s.unit_price).toLocaleString()} EGP`}
            </span>,
          )) : <div className="empty">Nothing was sold on this day.</div>}
        </>
      )}
      <div className="mact">
        <button className="pri" onClick={close}>Close</button>
      </div>
    </>
  );
}
