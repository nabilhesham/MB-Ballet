import { api, useApi } from '../api';
import { useToast } from '../components/Toast';
import DataTable from '../components/DataTable';
import Empty from '../components/Empty';
import { money } from './Products';

/** Archived products, with a way back — see Products.jsx's "Archived"
 *  button, the same convention the other three archived lists follow. */
export default function ArchivedProducts() {
  const { data: list, loading, error, reload } = useApi('/products?status=archived');
  const toast = useToast();

  if (loading) return <Empty>Loading…</Empty>;
  if (error) return <Empty>Could not load: {error.message}</Empty>;

  const unarchive = async p => {
    await api(`/products/${p.id}/unarchive`, { method: 'POST' });
    toast('Product restored');
    reload();
  };

  return (
    <>
      <div className="head">
        <div>
          <h1>Archived products</h1>
          <div className="sub">
            {list.length} archived — the sales stay exactly where they are, so
            a client who bought one still shows what they bought.
          </div>
        </div>
      </div>

      <div className="box pad0 dt-host">
        <DataTable
          rows={list} rowKey={r => r.id} search="Search archived products…"
          empty="Nothing archived."
          columns={[
            { label: 'NAME', sortValue: r => r.name, cell: r => r.name },
            {
              label: 'PRICE', className: 'num mute', sortValue: r => r.price ?? -1,
              cell: r => money(r.price),
            },
            {
              label: 'STOCK', className: 'num mute', hideSm: true,
              sortValue: r => r.stock, cell: r => r.stock,
            },
            {
              label: '', sortable: false, className: 'right',
              cell: r => <button className="sm" onClick={() => unarchive(r)}>Unarchive</button>,
            },
          ]}
        />
      </div>
    </>
  );
}
