/*
 * The classes a client is in, as a table cell.
 *
 * One component for the three lists that name clients — the Clients list,
 * a class's roster and a session's roster — so one person reads the same
 * wherever reception meets them, the way <Notes> does.
 *
 * **All of them, not the one.** A child taking Ballet and Flexibility is the
 * ordinary case at this academy, and a column that showed whichever plan
 * happened to expire first would change its answer as the dates moved. The
 * row comes from repo.classes_for(), which is every live plan's class.
 *
 * Each carries its class colour, the same dot the calendar, the class list
 * and the session page use — the colour is what makes a timetable readable
 * at a glance, and a roster is the other place the same classes are being
 * told apart.
 */
export default function Classes({ rows }) {
  if (!rows || !rows.length) return '—';
  return (
    <span className="classcell">
      {rows.map(c => (
        <span key={c.id} className="classchip">
          <span className="dot" style={{ background: c.colour }} />{c.name}
        </span>
      ))}
    </span>
  );
}
