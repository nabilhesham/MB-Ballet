import { useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { api, useApi } from '../api';
import { fmtISODay, fmtSize, fmtWhen, withSize } from '../lib/format';
import { useConfirm } from '../components/ConfirmModal';
import { useModal } from '../components/Modal';
import { useToast } from '../components/Toast';
import Avatar from '../components/Avatar';
import { Pill } from '../components/Pill';
import DataTable from '../components/DataTable';
import Empty from '../components/Empty';
import ProductForm from '../modals/ProductForm';
import SellProduct from '../modals/SellProduct';
import { StockPill, money } from './Products';

export default function ProductDetail() {
  const { id } = useParams();
  const { data: p, loading, error, reload } = useApi(`/products/${id}`);
  const { open } = useModal();
  const confirm = useConfirm();
  const toast = useToast();
  const nav = useNavigate();
  const photoInput = useRef(null);

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  // The shelf, one row per size. A product with no sizes has exactly one
  // row with none, so this is empty for it and there is nothing to break
  // down — IN STOCK beside it is already the whole answer.
  const sizes = (p.sizes || []).filter(z => z.size != null);

  const onPhotoChosen = async e => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`/api/products/${p.id}/photo`, { method: 'POST', body: fd });
    if (r.ok) { toast('Photo saved'); reload(); } else toast('Upload failed', 'bad');
  };

  const archive = () => confirm({
    title: 'Archive this product?',
    message: (
      <>
        <b>{p.name}</b> comes off the shop list. Every sale stays exactly
        where it is, so the clients who bought one still show what they
        bought — and it can be restored from the Archived list.
      </>
    ),
    label: 'Archive',
    onConfirm: async () => {
      await api(`/products/${p.id}`, { method: 'DELETE' });
      toast('Product archived');
      nav('/products');
    },
  });

  const removeSale = s => confirm({
    title: 'Remove this sale?',
    message: (
      <>
        {s.qty} × <b>{withSize(p.name, s.size)}</b> comes off{' '}
        <b>{s.client_name}</b>. The stock
        goes back on the shelf it came off and the money leaves
        {s.paid_on
          ? <> the takings for {fmtISODay(s.paid_on)}</>
          : ' nothing — it was unpaid'}.
      </>
    ),
    label: 'Remove',
    onConfirm: async () => {
      await api(`/product-sales/${s.id}`, { method: 'DELETE' });
      toast('Sale removed');
      reload();
    },
  });

  return (
    <>
      <div className="head">
        <div className="row" style={{ alignItems: 'flex-start', gap: 14 }}>
          {p.photo_path ? (
            <img
              src={p.photo_path} alt=""
              style={{
                width: 88, height: 88, objectFit: 'cover',
                borderRadius: 'var(--r-sm)',
              }}
            />
          ) : (
            <div className="ph" style={{ width: 88, height: 88, fontSize: 11 }}>
              no photo
            </div>
          )}
          <div>
            <h1>{p.name}</h1>
            <div className="sub" style={{ fontFamily: 'var(--mono)' }}>{money(p.price)}</div>
            <div className="row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
              <StockPill n={p.stock} />
              {sizes.map(z => (
                <Pill key={z.size} kind={z.qty ? 'grey' : 'bad'}>
                  {fmtSize(z.size)} · {z.qty}
                </Pill>
              ))}
            </div>
          </div>
        </div>
        <div className="row">
          <input type="file" accept="image/*" ref={photoInput} onChange={onPhotoChosen} hidden />
          <button onClick={() => photoInput.current?.click()}>Photo</button>
          <button onClick={() => open(<ProductForm existing={p} onSaved={reload} />)}>
            Edit
          </button>
          <button className="danger" onClick={archive}>Archive</button>
          <button
            className="pri" disabled={!p.stock}
            onClick={() => open(<SellProduct product={p} onSaved={reload} />)}
          >
            Sell to a student
          </button>
        </div>
      </div>

      {p.notes && <div className="box" style={{ marginBottom: 16 }}>{p.notes}</div>}

      <div className="grid g3" style={{ marginBottom: 16 }}>
        <div className="box kpi">
          <div className="k">IN STOCK</div>
          <div className="v" style={{ fontSize: 20, paddingTop: 8 }}>{p.stock}</div>
          {/* The figure is the sum of the sizes and has one writer -- see
              access._sync_stock(). Naming them under it is what makes "6 in
              stock" answerable at the counter, where the question is always
              "have you got a 36". */}
          <div className="n">
            {sizes.length
              ? sizes.map(z => `${fmtSize(z.size)}: ${z.qty}`).join(' · ')
              : 'one size'}
          </div>
        </div>
        <div className="box kpi">
          <div className="k">SOLD</div>
          <div className="v" style={{ fontSize: 20, paddingTop: 8 }}>{p.sold}</div>
          <div className="n">
            {p.sales.length} sale{p.sales.length === 1 ? '' : 's'}
          </div>
        </div>
        <div className="box kpi">
          <div className="k">TAKEN FOR IT</div>
          <div className="v" style={{ fontSize: 20, paddingTop: 8, color: 'var(--brand-deep)' }}>
            {p.sales
              .reduce((t, s) => t + (s.qty || 0) * (s.unit_price || 0), 0)
              .toLocaleString()}{' '}
            <span style={{ fontSize: 12, color: 'var(--mute)' }}>EGP</span>
          </div>
          {/* Each sale kept the price it was sold at, so this is what was
              actually taken rather than today's price times the count. */}
          <div className="n">at the price each sale was made</div>
        </div>
      </div>

      <h2>Who bought it</h2>
      <div className="box pad0 dt-host">
        <DataTable
          rows={p.sales} rowKey={r => r.id} search="Search by student…"
          empty="Nobody has bought this yet."
          columns={[
            {
              label: 'STUDENT', sortValue: r => r.client_name || '',
              cell: r => (
                <div className="row" style={{ gap: 8 }}>
                  <Avatar client={{ photo_path: r.photo_path, name_en: r.client_name }} />
                  <a href={`#/client/${r.client_id}`}>{r.client_name}</a>
                </div>
              ),
            },
            {
              label: 'SIZE', className: 'num mute',
              // -1 so "one size" sorts below every real size rather than
              // among them: it is not a number, and NaN would scatter it.
              sortValue: r => (r.size == null ? -1 : r.size),
              cell: r => fmtSize(r.size),
            },
            {
              label: 'QTY', className: 'num', sortValue: r => r.qty,
              cell: r => r.qty,
            },
            {
              label: 'PAID', className: 'num mute', sortValue: r => r.unit_price ?? -1,
              cell: r => (r.unit_price == null
                ? 'no price'
                : `${((r.qty || 0) * r.unit_price).toLocaleString()} EGP`),
            },
            {
              label: 'ON', className: 'mute', hideSm: true,
              sortValue: r => r.paid_on || '',
              cell: r => (r.paid_on ? fmtISODay(r.paid_on) : 'unpaid'),
            },
            {
              label: 'SOLD', className: 'mute num', hideSm: true,
              sortValue: r => r.created_at || 0,
              cell: r => (r.created_at ? fmtWhen(r.created_at) : '—'),
            },
            {
              label: '', sortable: false, className: 'right',
              cell: r => (
                <button className="sm danger" onClick={() => removeSale(r)}>Remove</button>
              ),
            },
          ]}
        />
      </div>
    </>
  );
}
