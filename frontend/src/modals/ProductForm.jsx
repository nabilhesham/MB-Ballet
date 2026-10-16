import { useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/**
 * New + edit product. Pass `existing` to edit, omit it to create.
 *
 * **The stock figure is the shelf, and the shelf is one row per size.** A
 * leotard comes in 34, 36 and 38 and a hairnet comes in none, so this form
 * has a switch rather than two forms: off, there is a single IN STOCK box
 * and the product is sold with no size named; on, there is a row per size
 * with its own amount. Underneath they are the same thing — a product with
 * no sizes holds exactly one shelf row whose size is NULL — which is what
 * lets a product gain sizes and lose them again without anything else in
 * the app knowing which it ever was. See access.set_product_sizes().
 *
 * A size is a number because the academy's are, and halves are kept: 34.5
 * rounded to 34 is the distinction the fitting was made on.
 */
export default function ProductForm({ existing, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [name, setName] = useState(existing?.name || '');
  // Kept as strings, not numbers: an empty price is a real state — a thing
  // whose price nobody has written down must not be reported as zero
  // revenue, the same rule a plan's price follows. The same goes for every
  // amount below, so a half-typed number is not read as a different one.
  const [price, setPrice] = useState(
    existing?.price != null ? String(existing.price) : '',
  );
  const [notes, setNotes] = useState(existing?.notes || '');

  // The shelf as it arrived. One row with no size means "no sizes", which
  // is the state the switch reads rather than a flag of its own: two
  // switches for one fact eventually disagree.
  const arrived = existing?.sizes || [];
  const arrivedSized = arrived.filter(s => s.size != null);
  const [sized, setSized] = useState(arrivedSized.length > 0);
  const [plain, setPlain] = useState(
    String(arrivedSized.length ? 0 : (arrived[0]?.qty ?? existing?.stock ?? 0)),
  );
  const [rows, setRows] = useState(
    arrivedSized.length
      ? arrivedSized.map(s => ({ size: String(s.size), qty: String(s.qty) }))
      : [{ size: '', qty: '0' }],
  );

  // Which sizes already have sales behind them. Such a size can go to zero
  // but cannot be removed — a sale has to be able to go back on the shelf
  // it came off — so the form says so instead of the save quietly doing
  // less than it said. See set_product_sizes()'s `kept`.
  const soldSizes = new Set(
    (existing?.sales || [])
      .filter(s => s.size != null)
      .map(s => String(s.size)),
  );

  const setRow = (i, field, v) =>
    setRows(rs => rs.map((r, j) => (j === i ? { ...r, [field]: v } : r)));
  const addRow = () => setRows(rs => [...rs, { size: '', qty: '0' }]);
  const dropRow = i => setRows(rs => (rs.length > 1
    ? rs.filter((_, j) => j !== i)
    : [{ size: '', qty: '0' }]));

  const total = sized
    ? rows.reduce((t, r) => t + (Number(r.qty) || 0), 0)
    : (Number(plain) || 0);

  const problem = () => {
    if (!name.trim()) return 'Name is required';
    if (price.trim() !== '' && !Number.isFinite(Number(price))) {
      return 'Price must be a number';
    }
    if (!sized) {
      const n = Number(plain);
      if (!Number.isInteger(n) || n < 0) {
        return 'Stock must be a whole number, zero or more';
      }
      return null;
    }
    const seen = new Set();
    for (const r of rows) {
      const size = Number(r.size);
      const qty = Number(r.qty);
      if (r.size.trim() === '' || !Number.isFinite(size) || size <= 0) {
        return 'Every size needs a number above zero';
      }
      if (!Number.isInteger(qty) || qty < 0) {
        return 'Every amount must be a whole number, zero or more';
      }
      if (seen.has(size)) return `Size ${size} is listed twice`;
      seen.add(size);
    }
    for (const s of soldSizes) {
      if (!rows.some(r => Number(r.size) === Number(s))) {
        return `Size ${s} has already been sold — set it to 0 rather than `
          + 'removing it, so a sale can go back on the shelf';
      }
    }
    return null;
  };

  const save = async () => {
    const bad = problem();
    if (bad) return toast(bad, 'bad');
    const body = {
      name,
      price: price.trim() === '' ? null : Number(price),
      notes: notes.trim() || null,
      sizes: sized
        ? rows.map(r => ({ size: Number(r.size), qty: Number(r.qty) }))
        : [{ size: null, qty: Number(plain) }],
    };
    try {
      if (existing) {
        const r = await api(`/products/${existing.id}`, { method: 'PUT', body });
        // One toast at a time, so what it kept back is the thing worth
        // saying: the save succeeded either way, and "Saved" over the top
        // of it would hide the one sentence that explains the shelf not
        // looking like what was typed. See set_product_sizes()'s `kept`.
        toast((r.kept || [])[0] || 'Saved', r.kept?.length ? 'bad' : 'ok');
      } else {
        await api('/products', { method: 'POST', body });
        toast('Product added');
      }
      close();
      onSaved();
    } catch (e) { toast(e.message, 'bad'); }
  };

  return (
    <>
      <h3>{existing ? 'Edit product' : 'New product'}</h3>
      <div className="mh">
        {existing
          ? 'Changing the price does not touch a sale already written down — '
            + 'each one kept what it cost on the day, so a past day’s '
            + 'takings stay honest.'
          : 'Add the picture from the product’s own page once it exists.'}
      </div>
      <label>NAME</label>
      <input value={name} onChange={e => setName(e.target.value)} placeholder="Leotard" />
      <div className="fieldrow">
        <div>
          <label>PRICE (EGP)</label>
          <input
            type="number" step="any" min="0" value={price}
            onChange={e => setPrice(e.target.value)} placeholder="leave blank if unpriced"
          />
        </div>
        {!sized && (
          <div>
            <label>IN STOCK</label>
            <input
              type="number" step="1" min="0" value={plain}
              onChange={e => setPlain(e.target.value)}
            />
          </div>
        )}
      </div>

      <label className="row" style={{ gap: 8, alignItems: 'center' }}>
        <input
          type="checkbox" checked={sized} style={{ width: 'auto' }}
          onChange={e => setSized(e.target.checked)}
        />
        THIS COMES IN SIZES
      </label>

      {sized && (
        <>
          <div className="row" style={{ gap: 8, marginTop: 6 }}>
            <label style={{ flex: 1, margin: 0 }}>SIZE</label>
            <label style={{ flex: 1, margin: 0 }}>HOW MANY</label>
            {/* Holds the two labels over their own inputs rather than over
                the Remove button, which is what a two-column header would
                do here. */}
            <span style={{ visibility: 'hidden' }}><button className="sm">Remove</button></span>
          </div>
          <div className="listcap" style={{ marginTop: 4 }}>
            {rows.map((r, i) => (
              /* Keyed by position, not by size: the size is what is being
                 typed, so keying on it would remount the input on every
                 keystroke and lose the cursor. */
              <div key={i} className="row" style={{ gap: 8, marginBottom: 6 }}>
                <input
                  type="number" step="any" min="0" placeholder="size"
                  value={r.size} onChange={e => setRow(i, 'size', e.target.value)}
                  style={{ flex: 1 }}
                />
                <input
                  type="number" step="1" min="0" placeholder="how many"
                  value={r.qty} onChange={e => setRow(i, 'qty', e.target.value)}
                  style={{ flex: 1 }}
                />
                <button
                  className="sm" onClick={() => dropRow(i)}
                  disabled={soldSizes.has(String(Number(r.size)))}
                  title={soldSizes.has(String(Number(r.size)))
                    ? 'Already sold in this size — set it to 0 instead'
                    : 'Remove this size'}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
          <button className="sm" onClick={addRow}>Add a size</button>
        </>
      )}

      <label>NOTES</label>
      <textarea value={notes} onChange={e => setNotes(e.target.value)} />

      <div className="mh">
        {problem() || (sized
          ? `${rows.length} size${rows.length === 1 ? '' : 's'}, `
            + `${total} on the shelf altogether.`
          : `${total} on the shelf. Sold with no size named.`)}
      </div>

      <div className="mact">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>Save</button>
      </div>
    </>
  );
}
