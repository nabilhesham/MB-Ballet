/** The .empty panel used for "nothing here yet" states outside a DataTable. */
export default function Empty({ title, children }) {
  return (
    <div className="empty">
      {title && <strong>{title}</strong>}
      {children}
    </div>
  );
}
