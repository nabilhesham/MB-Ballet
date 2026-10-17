import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useApi } from '../api';
import { useModal } from '../components/Modal';
import { Pill } from '../components/Pill';
import DataTable from '../components/DataTable';
import Empty from '../components/Empty';
import ProductForm from '../modals/ProductForm';
import { fmtISO, fmtSize, thisMonthBounds } from '../lib/format';

/** Price as a figure, or the fact that nobody wrote one down. */
export const money = v =>
  (v == null ? 'no price' : `${Number(v).toLocaleString()} EGP`);

/**
 * The stand-in for a product with no picture.
 *
 * It replaced the words "no photo", which were bare text — `ph` was never
 * a class in style.css — and read as a label rather than as an empty
 * frame. In a grid of cards the eye is scanning pictures, and a block of
 * words where every other card has an image is the one thing that stops
 * the scan. A drawn frame is the same shape, size and weight as a photo,
 * so the grid stays a grid.
 *
 * Inline SVG rather than a shipped file, for three reasons: it is four
 * shapes; it has to take the page's own tokens so it is right in both
 * themes (no hardcoded hex — see the conventions); and an asset that
 * failed to load would be the very failure it is here to depict.
 */
export function NoImage({ size = 64 }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 64 64" role="img"
      aria-label="No image" style={{
        flex: '0 0 auto', borderRadius: 'var(--r-sm)',
        background: 'var(--surface-2)', color: 'var(--dim)',
      }}
    >
      <title>No image</title>
      <rect
        x="13.25" y="17.25" width="37.5" height="29.5" rx="3.5"
        fill="none" stroke="currentColor" strokeWidth="2.25"
      />
      <circle cx="24" cy="27.5" r="3.25" fill="currentColor" />
      <path
        d="M17 42.5l8.5-8.5 6 6 5.5-4.5 8 7" fill="none"
        stroke="currentColor" strokeWidth="2.25"
        strokeLinejoin="round" strokeLinecap="round"
      />
      <path
        d="M15 49L49 15" stroke="currentColor" strokeWidth="2.25"
        strokeLinecap="round" opacity=".5"
      />
    </svg>
  );
}

/** What the shelf says. Zero is a real answer and reads as one. */
export function StockPill({ n }) {
  if (!n) return <Pill kind="bad">out of stock</Pill>;
  if (n <= 3) return <Pill kind="warn">{n} left</Pill>;
  return <Pill kind="ok">{n} in stock</Pill>;
}

/**
 * What went over the counter, between two dates.
 *
 * The product page answers "who bought this one" and the client profile
 * answers "what did she buy"; neither answers "what did we sell last
 * month", which is the question stock-taking and the month's figures are
 * actually asked of. It is one more round trip on this page and it is
 * fetched rather than filtered here, because a term's sales are not a list
 * already in hand the way a dozen products are.
 *
 * **Dated by PAID ON, the same column TAKEN TODAY reads**, so a day in this
 * list and a day in the till are the same day's money. An undated sale is
 * therefore in no range at all — said under the table rather than left to
 * be discovered, since the sale itself is still on the profile and on the
 * product's page.
 *
 * The range applies on a button and never on change, and lives in the
 * table's own bar: a date input fires on every edit, so binding the fetch
 * to it reloads the list for a half-typed year. See the .filterbar note in
 * CLAUDE.md.
 */
function History() {
  const [first, last] = thisMonthBounds();
  const [range, setRange] = useState({ start: first, end: last });
  const [draft, setDraft] = useState({ start: first, end: last });
  const qs = [range.start && `start=${range.start}`,
              range.end && `end=${range.end}`].filter(Boolean).join('&');
  const { data, loading, error } = useApi(`/products/history${qs ? `?${qs}` : ''}`);

  const dirty = draft.start !== range.start || draft.end !== range.end;
  const bounded = range.start || range.end;

  const bar = (
    <>
      <div className="filterbar" style={{ gap: 8 }}>
        <div>
          <label>FROM</label>
          <input
            type="date" value={draft.start}
            onChange={e => setDraft(d => ({ ...d, start: e.target.value }))}
          />
        </div>
        <div>
          <label>TO</label>
          <input
            type="date" value={draft.end}
            onChange={e => setDraft(d => ({ ...d, end: e.target.value }))}
          />
        </div>
        <button className="pri" disabled={!dirty} onClick={() => setRange(draft)}>
          Apply
        </button>
        <button
          disabled={!bounded}
          onClick={() => { setDraft({ start: '', end: '' }); setRange({ start: '', end: '' }); }}
        >
          Show all
        </button>
      </div>
    </>
  );

  return (
    <div style={{ marginTop: 28 }}>
      <h2>Purchase history</h2>
      <div className="sub" style={{ marginBottom: 10 }}>
        {error ? `Could not load: ${error.message}`
          : loading ? 'Loading…'
            : `${data.sales.length} sale${data.sales.length === 1 ? '' : 's'}`
              + ` · ${data.items} item${data.items === 1 ? '' : 's'}`
              + ` · ${data.paid.toLocaleString()} EGP`
              + (data.unpriced
                ? ` · ${data.unpriced} with no price written down, counted but`
                  + ' not added'
                : '')}
      </div>
      <div className="box pad0 dt-host">
        <DataTable
          rows={data ? data.sales : []} rowKey={r => r.id}
          search="Search by student or product…" bar={bar}
          empty={bounded
            ? 'Nothing was sold in that range. A sale with no payment date'
              + ' yet is in no range — it is on the student’s profile and the'
              + ' product’s page.'
            : 'Nothing has been sold yet.'}
          columns={[
            {
              label: 'PRODUCT', sortValue: r => r.product_name || '',
              cell: r => (
                <a href={`#/product/${r.product_id}`}>{r.product_name}</a>
              ),
            },
            {
              label: 'SIZE', className: 'num mute',
              // -1 keeps "one size" below the real sizes rather than
              // scattered among them.
              sortValue: r => (r.size == null ? -1 : r.size),
              cell: r => fmtSize(r.size),
            },
            {
              label: 'STUDENT', sortValue: r => r.client_name || '',
              cell: r => <a href={`#/client/${r.client_id}`}>{r.client_name}</a>,
            },
            {
              label: 'QTY', className: 'num', sortValue: r => r.qty,
              cell: r => r.qty,
            },
            {
              label: 'PAID', className: 'num mute',
              sortValue: r => r.unit_price ?? -1,
              cell: r => (r.unit_price == null
                ? 'no price'
                : `${((r.qty || 0) * r.unit_price).toLocaleString()} EGP`),
            },
            {
              label: 'ON', className: 'mute', sortValue: r => r.paid_on || '',
              cell: r => fmtISO(r.paid_on),
            },
          ]}
        />
      </div>
    </div>
  );
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
                ) : <NoImage />}
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 16, fontWeight: 600 }}>{p.name}</div>
                  <div className="sub" style={{ fontFamily: 'var(--mono)' }}>
                    {money(p.price)}
                  </div>
                </div>
              </div>
              <div className="row" style={{ marginTop: 12, flexWrap: 'wrap' }}>
                <StockPill n={p.stock} />
                {/* The breakdown, when there is one: the figure beside it is
                    the sum of these, and "6 in stock" across three sizes
                    answers a different question from "6 in a 36". A product
                    with no sizes has one shelf row and nothing to break
                    down, so nothing is shown for it. */}
                {(p.sizes || []).filter(z => z.size != null).map(z => (
                  <Pill key={z.size} kind={z.qty ? 'grey' : 'bad'}>
                    {fmtSize(z.size)} · {z.qty}
                  </Pill>
                ))}
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

      <History />
    </>
  );
}
