import { useState } from 'react';

import { initials } from '../lib/format';

/**
 * Photo if the client has one, otherwise their initials.
 *
 * The fallback is also what happens when the photo *fails*, not only when
 * there is none: `photo_path` is a URL this app answers, and it can point at
 * something that is gone — a legacy `/photos/...` path on a database opened
 * before its photos/ folder was read in, or a row lost in a backend move. A
 * bare <img> on a 404 shows the browser's broken-image glyph, which reads as
 * a fault in the app rather than as a client with no picture.
 */
export default function Avatar({ client, big = false }) {
  const [failed, setFailed] = useState(false);
  const cls = 'avatar' + (big ? ' lg' : '');
  return client.photo_path && !failed
    ? <img className={cls} src={client.photo_path} alt="" onError={() => setFailed(true)} />
    : <span className={cls}>{initials(client.name_en || client.name)}</span>;
}
