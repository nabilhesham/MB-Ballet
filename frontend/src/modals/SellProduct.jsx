import { useEffect, useRef, useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import { todayISO } from '../lib/format';
import { money } from '../views/Products';

/**
 * Sell a product to a client — one modal, used from both ends.
 *
 * Pass `product` and it searches for the student; pass `client` and it
 * searches the products. The two are the same transaction entered from
 * whichever screen reception happens to be on, and a second copy of the
 * quantity, price and date logic is how the two would eventually disagree
 * about what a sale is.
 *
 * The client search goes through the admin's own `/clients?q=`, the same
 * endpoint the kiosk's name lookup uses, so what this finds is what the
 * Clients page would — a second search with its own idea of a match would
 * drift from it. The product list arrives whole and is filtered here,
 * because there are a dozen of them (see Products.jsx).
 */
export default function SellProduct({ product, client, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [products, setProducts] = useState(null);
  const [pickedProduct, setPickedProduct] = useState(product || null);
  const [pickedClient, setPickedClient] = useState(client || null);
  const [q, setQ] = useState('');
  const [found, setFound] = useState([]);
  const [qty, setQty] = useState('1');
  // Defaults to today because a sale over the counter is paid as it
  // happens. Cleared, it saves unpaid and falls out of every day's till,
  // exactly as an unpaid plan does.
  const [paidOn, setPaidOn] = useState(todayISO());
  const [saving, setSaving] = useState(false);

  // The product list, once, when this modal is the one that has to choose.
  useEffect(() => {
    if (product) return;
    api('/products').then(setProducts).catch(() => setProducts([]));
  }, [product]);

  // Live client search: two characters, debounced, and an answer that newer
  // keystrokes have overtaken is dropped — the same three rules the kiosk's
  // name search follows.
  const seq = useRef(0);
  useEffect(() => {
    if (client) return undefined;
    const needle = q.trim();
    if (needle.length < 2) { setFound([]); return undefined; }
    const mine = ++seq.current;
    const id = setTimeout(() => {
      api(`/clients?q=${encodeURIComponent(needle)}`)
        .then(rows => { if (mine === seq.current) setFound(rows.slice(0, 8)); })
        .catch(() => {});
    }, 200);
    return () => clearTimeout(id);
  }, [q, client]);

  const shownProducts = (() => {
    if (!products) return [];
    const needle = q.trim().toLowerCase();
    return (needle
      ? products.filter(p => (p.name || '').toLowerCase().includes(needle))
      : products).slice(0, 8);
  })();

  const n = Number(qty);
  const stock = pickedProduct ? pickedProduct.stock : 0;
  const total = pickedProduct && pickedProduct.price != null
    ? pickedProduct.price * (Number.isFinite(n) ? n : 0) : null;
  const ready = pickedProduct && pickedClient && Number.isInteger(n) && n >= 1
    && n <= stock;

  const save = async () => {
    if (!ready || saving) return;
    setSaving(true);
    try {
      await api(`/products/${pickedProduct.id}/sell`, {
        method: 'POST',
        body: { client_id: pickedClient.id, qty: n, paid_on: paidOn },
      });
      toast(`${n} × ${pickedProduct.name} sold to ${pickedClient.name_en}`);
      close();
      onSaved();
    } catch (e) {
      toast(e.message, 'bad');
      setSaving(false);
    }
  };

  return (
    <>
      <h3>Sell a product</h3>
      <div className="mh">
        The stock comes off the shelf and the money lands on the day in PAID
        ON. Removing the sale later gives both of them back.
      </div>

      {/* Whichever half is already known is shown; the other is searched. */}
      {product
        ? (
          <>
            <label>PRODUCT</label>
            <div className="pickrow on" style={{ cursor: 'default' }}>
              <span className="pk-class">{product.name}</span>
              <span className="pk-meta">{money(product.price)} · {product.stock} in stock</span>
            </div>
          </>
        )
        : (
          <>
            <label>PRODUCT</label>
            {pickedProduct ? (
              <div className="row" style={{ marginBottom: 8 }}>
                <div className="pickrow on" style={{ cursor: 'default', flex: 1 }}>
                  <span className="pk-class">{pickedProduct.name}</span>
                  <span className="pk-meta">
                    {money(pickedProduct.price)} · {pickedProduct.stock} in stock
                  </span>
                </div>
                <button className="sm" onClick={() => { setPickedProduct(null); setQ(''); }}>
                  Change
                </button>
              </div>
            ) : (
              <>
                <input
                  className="search" type="search" placeholder="Search by product name…"
                  value={q} onChange={e => setQ(e.target.value)}
                />
                {products === null ? (
                  <div className="empty">Loading…</div>
                ) : shownProducts.length ? (
                  <div className="picklist" style={{ maxHeight: '26vh' }}>
                    {shownProducts.map(p => (
                      <label
                        key={p.id} className="pickrow"
                        onClick={() => { setPickedProduct(p); setQ(''); }}
                      >
                        <span className="pk-class">{p.name}</span>
                        <span className="pk-meta">
                          {money(p.price)} · {p.stock ? `${p.stock} in stock` : 'out of stock'}
                        </span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <div className="empty">No product matches that search.</div>
                )}
              </>
            )}
          </>
        )}

      {client
        ? (
          <>
            <label>CLIENT</label>
            <div className="pickrow on" style={{ cursor: 'default' }}>
              <span className="pk-class">{client.name_en}</span>
              <span className="pk-meta">{client.phone || 'no number'}</span>
            </div>
          </>
        )
        : (
          <>
            <label>STUDENT</label>
            {pickedClient ? (
              <div className="row" style={{ marginBottom: 8 }}>
                <div className="pickrow on" style={{ cursor: 'default', flex: 1 }}>
                  <span className="pk-class">{pickedClient.name_en}</span>
                  <span className="pk-meta">{pickedClient.phone || 'no number'}</span>
                </div>
                <button className="sm" onClick={() => { setPickedClient(null); setQ(''); }}>
                  Change
                </button>
              </div>
            ) : (
              <>
                <input
                  className="search" type="search" placeholder="Search by name or number…"
                  value={q} onChange={e => setQ(e.target.value)}
                />
                {found.length ? (
                  <div className="picklist" style={{ maxHeight: '26vh' }}>
                    {found.map(c => (
                      <label
                        key={c.id} className="pickrow"
                        onClick={() => { setPickedClient(c); setQ(''); }}
                      >
                        <span className="pk-class">{c.name_en}</span>
                        {/* The number, because a name on its own does not
                            pick anybody out — a parent's two children sit
                            next to each other in this list. */}
                        <span className="pk-meta">{c.phone || 'no number'}</span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <div className="empty">
                    {q.trim().length < 2
                      ? 'Type two letters of their name.'
                      : 'Nobody matches that search.'}
                  </div>
                )}
              </>
            )}
          </>
        )}

      <div className="fieldrow">
        <div>
          <label>HOW MANY</label>
          <input
            type="number" step="1" min="1" max={stock || 1} value={qty}
            onChange={e => setQty(e.target.value)}
          />
        </div>
        <div>
          <label>PAID ON</label>
          <input type="date" value={paidOn} onChange={e => setPaidOn(e.target.value)} />
        </div>
      </div>

      <div className="mh">
        {!pickedProduct ? 'Pick a product.'
          : !pickedClient ? 'Pick a student.'
            : !stock ? `${pickedProduct.name} is out of stock — add stock first.`
              : !Number.isInteger(n) || n < 1 ? 'How many? One or more.'
                : n > stock ? `Only ${stock} in stock.`
                  : total == null
                    ? `${n} × ${pickedProduct.name} — no price written down, so `
                      + 'it is counted but adds nothing to the day’s money.'
                    : `${n} × ${pickedProduct.name} — ${total.toLocaleString()} EGP`
                      + (paidOn ? '' : ', unpaid')}
      </div>

      <div className="mact">
        <button onClick={close}>Cancel</button>
        <button className="pri" disabled={!ready || saving} onClick={save}>
          {saving ? 'Saving…' : 'Sell'}
        </button>
      </div>
    </>
  );
}
