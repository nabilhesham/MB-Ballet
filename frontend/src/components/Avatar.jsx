import { initials } from '../lib/format';

/** Photo if the client has one, otherwise their initials. */
export default function Avatar({ client, big = false }) {
  const cls = 'avatar' + (big ? ' lg' : '');
  return client.photo_path
    ? <img className={cls} src={client.photo_path} alt="" />
    : <span className={cls}>{initials(client.name_en || client.name)}</span>;
}
