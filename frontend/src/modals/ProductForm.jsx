import { useState } from 'react';

import { api } from '../api';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';

/** New + edit product. Pass `existing` to edit, omit it to create. */
export default function ProductForm({ existing, onSaved }) {
  const { close } = useModal();
  const toast = useToast();

  const [name, setName] = useState(existing?.name || '');
  // Kept as strings, not numbers: an empty price is a real state — a thing
  // whose price nobody has written down must not be reported as zero
  // revenue, the same rule a plan's price follows.
  const [price, setPrice] = useState(
    existing?.price != null ? String(existing.price) : '',
  );
  const [stock, setStock] = useState(String(existing?.stock ?? 0));
  const [notes, setNotes] = useState(existing?.notes || '');

  const save = async () => {
    if (!name.trim()) return toast('Name is required', 'bad');
    const body = {
      name,
      price: price.trim() === '' ? null : Number(price),
      stock: stock.trim() === '' ? 0 : Number(stock),
      notes: notes.trim() || null,
    };
    if (Number.isNaN(body.price)) return toast('Price must be a number', 'bad');
    if (!Number.isInteger(body.stock) || body.stock < 0) {
      return toast('Stock must be a whole number, zero or more', 'bad');
    }
    try {
      if (existing) {
        await api(`/products/${existing.id}`, { method: 'PUT', body });
        toast('Saved');
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
        <div>
          <label>IN STOCK</label>
          <input
            type="number" step="1" min="0" value={stock}
            onChange={e => setStock(e.target.value)}
          />
        </div>
      </div>
      <label>NOTES</label>
      <textarea value={notes} onChange={e => setNotes(e.target.value)} />
      <div className="mact">
        <button onClick={close}>Cancel</button>
        <button className="pri" onClick={save}>Save</button>
      </div>
    </>
  );
}
