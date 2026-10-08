/*
 * A client's note, in a table cell.
 *
 * One component for three tables (Clients, a class's roster, a session's
 * roster) because a note is free text of no fixed length: wrapped, one long
 * note makes every row of the table tall and pushes the columns people are
 * actually scanning off the screen. Capped to one line with the whole thing
 * on hover, which is the same trade the kiosk's name search makes with the
 * mobile under a name.
 *
 * The note is about the *person* — `clients.notes`, not the plan's own note,
 * which belongs to one purchase (see the data model in CLAUDE.md). Reception
 * writes things in it they need at the counter, and until now the only way
 * to read one was to open the profile.
 */
export default function Notes({ text }) {
  if (!text) return '—';
  return <span className="notecell" title={text}>{text}</span>;
}
