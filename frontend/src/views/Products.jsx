import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useApi } from '../api';
import { useModal } from '../components/Modal';
import { Pill } from '../components/Pill';
import Empty from '../components/Empty';
import ProductForm from '../modals/ProductForm';

/** Price as a figure, or the fact that nobody wrote one down. */
export const money = v =>
  (v == null ? 'no price' : `${Number(v).toLocaleString()} EGP`);

/** What the shelf says. Zero is a real answer and reads as one. */
export function StockPill({ n }) {
  if (!n) return <Pill kind="bad">out of stock</Pill>;
  if (n <= 3) return <Pill kind="warn">{n} left</Pill>;
  return <Pill kind="ok">{n} in stock</Pill>;
}

/**
 * The shop. Cards rather than a table, the same shape the Classes list
 * uses — a product is a thing with a picture, and a row of text is a poor
 * way to find one.
 *
 * The search box filters what is already loaded, unlike the Clients list's:
 * the academy sells a dozen things, so the whole list is one round trip and
 * a server-side `q` would filter a different set of rows from the one on
 * screen. See api/products.py, where the same reasoning is written down
 * from the other end.
 */
export default function Products() {
  const { data: products, loading, error, reload } = useApi('/products');
  const { open } = useModal();
  const nav = useNavigate();
  const [query, setQuery] = useState('');

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const q = query.trim().toLowerCase();
  const shown = q
    ? products.filter(p => [p.name, p.notes]
      .some(v => (v || '').toLowerCase().includes(q)))
    : products;

  return (
    <>
      <div className="head">
        <div>
          <h1>Products</h1>
          <div className="sub">{products.length} on sale</div>
        </div>
        <div className="row">
          <button onClick={() => nav('/products/archived')}>Archived</button>
          <button className="pri" onClick={() => open(<ProductForm onSaved={reload} />)}>
            New product
          </button>
        </div>
      </div>

      {products.length > 0 && (
        <div className="box pad0" style={{ marginBottom: 16 }}>
          <div className="dt-bar">
            <input
              className="search dt-find" type="search"
              placeholder="Search by product name…"
              value={query} onChange={e => setQuery(e.target.value)}
            />
            <span className="dt-count">
              {q ? `${shown.length} of ${products.length}` : `${products.length} products`}
            </span>
          </div>
        </div>
      )}

      {shown.length ? (
        <div className="grid g3">
          {shown.map(p => (
            <div
              key={p.id} className="box tap" onClick={() => nav(`/product/${p.id}`)}
            >
              <div className="row" style={{ alignItems: 'flex-start', gap: 12 }}>
                {p.photo_path ? (
                  <img
                    src={p.photo_path} alt=""
                    style={{
                      width: 64, height: 64, objectFit: 'cover',
                      borderRadius: 'var(--r-sm)', flex: '0 0 auto',
                    }}
                  />
                ) : (
                  <div
                    className="ph"
                    style={{ width: 64, height: 64, flex: '0 0 auto', fontSize: 11 }}
                  >
                    no photo
                  </div>
                )}
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 16, fontWeight: 600 }}>{p.name}</div>
                  <div className="sub" style={{ fontFamily: 'var(--mono)' }}>
                    {money(p.price)}
                  </div>
                </div>
              </div>
              <div className="row" style={{ marginTop: 12 }}>
                <StockPill n={p.stock} />
              </div>
              {p.notes && (
                <div className="sub" style={{ marginTop: 10 }}>{p.notes}</div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <Empty>
          {products.length
            ? 'No product matches that search.'
            : 'Nothing on sale yet. Add the first product.'}
        </Empty>
      )}
    </>
  );
}
