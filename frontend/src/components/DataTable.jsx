/*
 * Controlled replacement for static/app.js's enhanceTables() (see the plan's
 * D7). The old mechanism worked by mutating already-rendered DOM — wrapper
 * divs via insertBefore, rows reordered by appendChild on click. Every one of
 * those techniques fights React, which owns this subtree and reconciles
 * against its own virtual tree on the next render. So this is a redesign,
 * not a transliteration: same visible behaviour, same CSS classes
 * (.dt/.dt-bar/.dt-scroll/.dt-count/.dt-find/.dt-caret/.dt-none), state lives
 * in React instead of being read back off the DOM.
 *
 * columns: [{ label, cell: row => node, sortValue?: row => value,
 *              sortable?: false, className?, hideSm?, style?, onCellClick? }]
 * onCellClick puts the click/hover behaviour on one cell instead of the
 * whole row — matches the handful of tables (Cards, Clients) where only the
 * name cell (and a separate button) navigate, not a row-wide click.
 * A column sorts only if it has a sortValue — matching the plan's cleaner
 * "operate on the row object, not rendered text" replacement for the old
 * per-cell data-sort override.
 *
 * rows.length === 0 renders the caller's `empty` message as a plain
 * `.empty` panel instead of a table — same as the old view markup choosing
 * between a <table> and a <div class="empty"> before any enhancement ran.
 * A search that matches nothing keeps the table and shows the "Nothing
 * matches" furniture row, exactly as before.
 */
import { useMemo, useState } from 'react';

const DT_SCROLL_ROWS = 12;

function rowSearchText(row) {
  return Object.values(row)
    .filter(v => v !== null && v !== undefined && typeof v !== 'object')
    .join(' ')
    .toLowerCase();
}

export default function DataTable({
  rows, rowKey, onRowClick, search, empty, columns, scrollRows = DT_SCROLL_ROWS,
}) {
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState(null); // { index, dir: 'asc' | 'desc' }

  const filtered = useMemo(() => {
    if (!search || !query.trim()) return rows;
    const q = query.trim().toLowerCase();
    return rows.filter(r => rowSearchText(r).includes(q));
  }, [rows, query, search]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns[sort.index];
    if (!col || typeof col.sortValue !== 'function') return filtered;
    const sign = sort.dir === 'asc' ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const x = col.sortValue(a), y = col.sortValue(b);
      return x < y ? -sign : x > y ? sign : 0;
    });
  }, [filtered, sort, columns]);

  if (!rows.length) {
    return <div className="empty">{empty}</div>;
  }

  const capped = sorted.length > scrollRows;

  return (
    <div className="dt">
      {search && (
        <div className="dt-bar">
          <input
            className="search dt-find"
            type="search"
            placeholder={search}
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          <span className="dt-count">
            {query.trim()
              ? `${sorted.length} of ${rows.length}`
              : `${rows.length} row${rows.length === 1 ? '' : 's'}`}
          </span>
        </div>
      )}
      <div className={'dt-scroll' + (capped ? ' capped' : '')}>
        <table>
          <thead>
            <tr>
              {columns.map((col, i) => {
                const sortable = col.sortable !== false && typeof col.sortValue === 'function';
                const dir = sort && sort.index === i ? sort.dir : undefined;
                return (
                  <th
                    key={col.label || i}
                    className={col.hideSm ? 'hide-sm' : undefined}
                    data-sortable={sortable ? '' : undefined}
                    data-dir={dir}
                    onClick={sortable ? () => setSort(s => ({
                      index: i,
                      dir: s && s.index === i && s.dir === 'asc' ? 'desc' : 'asc',
                    })) : undefined}
                  >
                    {col.label}
                    {sortable && <i className="dt-caret" />}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="dt-none">
                  Nothing matches that search.
                </td>
              </tr>
            ) : (
              sorted.map(r => (
                <tr
                  key={rowKey(r)}
                  className={onRowClick ? 'click' : undefined}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                >
                  {columns.map((col, i) => (
                    <td
                      key={col.label || i}
                      className={
                        [col.className, col.hideSm ? 'hide-sm' : '', col.onCellClick ? 'click' : '']
                          .filter(Boolean).join(' ') || undefined
                      }
                      style={col.style}
                      onClick={col.onCellClick ? e => { e.stopPropagation(); col.onCellClick(r); } : undefined}
                    >
                      {col.cell(r)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
