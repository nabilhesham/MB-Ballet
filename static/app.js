/* MB Ballet Academy — single-page admin.
   No build step by design: this is copied to a laptop and run. */

const $  = s => document.querySelector(s);
const view = $('#view'), veil = $('#veil'), modal = $('#modal');

/* ---------------------------------------------------------- utilities */
const api = async (path, opts={}) => {
  const r = await fetch('/api'+path, {
    headers:{'Content-Type':'application/json'}, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined
  });
  const data = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(data.error || data.detail || 'request failed');
  return data;
};

const esc = s => String(s ?? '').replace(/[<>&"]/g, c =>
  ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));

const fmtTime = ts => new Date(ts*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
const fmtDate = ts => new Date(ts*1000).toLocaleDateString([], {day:'numeric',month:'short'});
const fmtFull = ts => new Date(ts*1000).toLocaleString([], {weekday:'short',day:'numeric',
  month:'short',hour:'2-digit',minute:'2-digit'});
const fmtDay  = ts => new Date(ts*1000).toLocaleDateString([], {weekday:'short',day:'numeric',month:'short'});
const todayISO = () => new Date().toISOString().slice(0,10);
const initials = n => (n||'?').split(' ').slice(0,2).map(w=>w[0]).join('').toUpperCase();
const hrs = h => (h % 1 === 0 ? h : h.toFixed(1)) + (h === 1 ? ' hour' : ' hours');
/* "2026-09" -> "September". Parsed as a local date, not a UTC one: new
   Date('2026-09') is midnight UTC and reads as August in Alexandria. */
const monthName = m => new Date(+m.slice(0,4), +m.slice(5,7)-1, 1)
  .toLocaleDateString([], {month:'long'});
const fmtISO = d => d ? new Date(+d.slice(0,4), +d.slice(5,7)-1, +d.slice(8,10))
  .toLocaleDateString([], {day:'numeric',month:'short'}) : '—';

function avatar(c, big=false){
  const cls = 'avatar' + (big?' lg':'');
  return c.photo_path
    ? `<img class="${cls}" src="${esc(c.photo_path)}" alt="">`
    : `<span class="${cls}">${esc(initials(c.name_en||c.name))}</span>`;
}

let toastTimer;
function toast(msg, kind='ok'){
  const t = $('#toast');
  t.textContent = msg; t.className = 'toast on '+kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(()=>t.className='toast', 2800);
}

/* ---------------------------------------------------------- data tables
   Every table in the app is plain server-rendered markup. This upgrades it in
   place: click a header to sort, type to filter, and long tables scroll inside
   themselves with the header staying put.

   Deliberately not a library. This folder is copied to a reception laptop that
   may have no internet and nothing installed, so a CDN script tag would mean
   the admin screens silently lose sorting and searching exactly where they are
   needed most. It is also the reason the rows keep their own markup — the
   onclick handlers, pills and hide-sm columns all still work, because nothing
   here rewrites a cell.

   A table opts into the search box with data-search="placeholder text".
   Everything else gets sorting and the sticky header for free. */

const DT_SCROLL_ROWS = 12;        // taller than this and the body scrolls

function enhanceTables(root){
  root.querySelectorAll('table:not([data-dt])').forEach(table => {
    table.dataset.dt = '1';
    const body = table.tBodies[0];
    if(!body || !body.rows.length) return;

    // Wrap: .dt > [.dt-bar] + .dt-scroll > table
    const wrap = document.createElement('div');
    wrap.className = 'dt';
    table.parentNode.insertBefore(wrap, table);
    const scroll = document.createElement('div');
    scroll.className = 'dt-scroll';
    if(body.rows.length > DT_SCROLL_ROWS) scroll.classList.add('capped');
    wrap.appendChild(scroll);
    scroll.appendChild(table);
    if(wrap.parentNode.classList.contains('pad0'))
      wrap.parentNode.classList.add('dt-host');

    const rows = () => Array.from(body.rows).filter(r => !r.dataset.dtNote);
    dtSortable(table, body);
    if(table.dataset.search !== undefined) dtSearchable(table, body, wrap, rows);
  });
}

/* The value a cell sorts on. A cell may carry data-sort to override what is
   printed — the WHEN columns use it so "Wed, Sep 2" orders by its timestamp
   and not by the letter W. */
function dtKey(row, i){
  const cell = row.cells[i];
  if(!cell) return '';
  const raw = cell.dataset.sort ?? cell.textContent.trim();
  const n = Number(String(raw).replace(/[,\s]/g, ''));
  return (raw !== '' && !Number.isNaN(n)) ? n : String(raw).toLowerCase();
}

function dtSortable(table, body){
  const head = table.tHead && table.tHead.rows[0];
  if(!head) return;

  Array.from(head.cells).forEach((th, i) => {
    // An empty header is the avatar column; data-nosort marks action columns.
    if(!th.textContent.trim() || th.dataset.nosort !== undefined) return;
    th.dataset.sortable = '';
    th.insertAdjacentHTML('beforeend', '<i class="dt-caret"></i>');
    th.addEventListener('click', () => {
      const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
      Array.from(head.cells).forEach(o => delete o.dataset.dir);
      th.dataset.dir = dir;
      const sign = dir === 'asc' ? 1 : -1;
      // The "nothing matches" row is furniture, not data: it must not be
      // sorted into the middle of the table, and it stays at the bottom.
      const note = body.querySelector('tr[data-dt-note]');
      Array.from(body.rows)
        .filter(r => !r.dataset.dtNote)
        .sort((a, b) => {
          const x = dtKey(a, i), y = dtKey(b, i);
          return x < y ? -sign : x > y ? sign : 0;
        })
        .forEach(r => body.appendChild(r));
      if(note) body.appendChild(note);
    });
  });
}

function dtSearchable(table, body, wrap, rows){
  const bar = document.createElement('div');
  bar.className = 'dt-bar';
  bar.innerHTML =
    `<input class="search dt-find" type="search" placeholder="${
      esc(table.dataset.search || 'Search…')}">` +
    `<span class="dt-count"></span>`;
  wrap.insertBefore(bar, wrap.firstChild);

  const note = document.createElement('tr');
  note.dataset.dtNote = '1';
  note.hidden = true;
  note.innerHTML = `<td colspan="${table.tHead ? table.tHead.rows[0].cells.length : 1}"
                        class="dt-none">Nothing matches that search.</td>`;
  body.appendChild(note);

  const count = bar.querySelector('.dt-count');
  const find = bar.querySelector('.dt-find');
  const all = rows();

  const apply = () => {
    const q = find.value.trim().toLowerCase();
    let shown = 0;
    all.forEach(r => {
      const hit = !q || r.textContent.toLowerCase().includes(q);
      r.hidden = !hit;
      if(hit) shown++;
    });
    note.hidden = shown > 0;
    count.textContent = q ? `${shown} of ${all.length}`
                          : `${all.length} row${all.length === 1 ? '' : 's'}`;
  };
  find.addEventListener('input', apply);
  apply();
}

function openModal(html, wide=false){
  modal.innerHTML = html;
  modal.className = 'modal' + (wide ? ' wide' : '');
  veil.classList.add('on');
  enhanceTables(modal);
  const first = modal.querySelector('input,select,textarea');
  if(first) first.focus();
}
function closeModal(){ veil.classList.remove('on'); modal.innerHTML=''; }
veil.addEventListener('click', e => { if(e.target===veil) closeModal(); });
document.addEventListener('keydown', e => { if(e.key==='Escape') closeModal(); });

const val = id => { const e = $('#'+id); return e ? e.value.trim() : ''; };
const numval = id => { const e = $('#'+id); if(!e) return null;
  const v = e.value; return v===''?null:Number(v); };

function confirmBox(title, message, label, fn, danger=true){
  window._confirmFn = fn;
  openModal(`<h3>${esc(title)}</h3>
    <div class="mh" style="margin:8px 0 0;line-height:1.7">${message}</div>
    <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="${danger?'danger':'pri'}" onclick="runConfirm()">${esc(label)}</button></div>`);
}
window.runConfirm = async () => {
  const fn = window._confirmFn; closeModal();
  try{ await fn(); }catch(e){ toast(e.message,'bad'); }
};

/* Sessions-left pill, used in several tables. */
function balancePill(row){
  if(row.remaining === null || row.remaining === undefined)
    return '<span class="pill grey">no plan</span>';
  if(row.expires_on && row.expires_on < todayISO())
    return '<span class="pill bad">expired</span>';
  if(row.remaining <= 0) return '<span class="pill bad">0 left</span>';
  if(row.remaining <= 2) return `<span class="pill warn">${row.remaining} left</span>`;
  return `<span class="pill ok">${row.remaining} left</span>`;
}

function statusPill(status){
  if(status === 'present') return '<span class="pill ok">present</span>';
  if(status === 'absent')  return '<span class="pill bad">absent</span>';
  return '<span class="pill info">booked</span>';
}

/* ---------------------------------------------------------- router */
const routes = {};
function route(path, fn){ routes[path] = fn; }

async function render(){
  const hash = location.hash.slice(1) || '/';
  const [path, param] = hash.split('/').filter(Boolean).length > 1 && /^\d+$/.test(hash.split('/').pop())
    ? [hash.replace(/\/\d+$/,''), hash.split('/').pop()]
    : [hash, null];

  let active = null;
  document.querySelectorAll('nav a[data-r]').forEach(a => {
    const on = a.dataset.r === path || (path.startsWith(a.dataset.r) && a.dataset.r !== '/');
    a.classList.toggle('on', on);
    if(on) active = a;
  });
  const title = $('#pageTitle');
  if(title) title.textContent = active ? active.textContent.trim() : 'MB Ballet';
  window.scrollTo(0,0);

  const fn = routes[path] || routes['/'];
  view.innerHTML = '<div class="empty">Loading…</div>';
  try { await fn(param); enhanceTables(view); }
  catch(e){ view.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; }
}
window.addEventListener('hashchange', render);

setInterval(()=>{ const c = $('#clock'); if(c) c.textContent =
  new Date().toLocaleString([], {weekday:'short',day:'numeric',month:'short',
    hour:'2-digit',minute:'2-digit'}); }, 1000);

/* mobile drawer */
{
  const burger = $('#burger'), scrim = $('#scrim');
  const closeNav = () => document.body.classList.remove('nav-open');
  if(burger) burger.onclick = () => document.body.classList.toggle('nav-open');
  if(scrim) scrim.onclick = closeNav;
  document.querySelectorAll('nav a').forEach(a => a.addEventListener('click', closeNav));
  document.addEventListener('keydown', e => { if(e.key==='Escape') closeNav(); });
}

/* ============================================================ DASHBOARD */
route('/', async () => {
  const d = await api('/dashboard');
  const s = d.stats;
  const now = Date.now()/1000;

  const sessionRow = x => {
    const live = x.starts_at <= now && x.starts_at + x.duration_hours*3600 > now;
    const state = x.status !== 'scheduled'
      ? `<span class="pill grey">${x.status}</span>`
      : (live ? '<span class="pill ok">now</span>' : '');
    const pct = x.booked ? Math.round(100*x.attended/x.booked) : 0;
    return `<tr class="click" onclick="location.hash='#/session/${x.id}'">
      <td class="num mute" data-sort="${x.starts_at}">${fmtTime(x.starts_at)}</td>
      <td><span class="dot" style="background:${esc(x.colour)}"></span>${esc(x.class_name)} ${state}</td>
      <td class="mute hide-sm">${esc(x.instructor_name||'—')}</td>
      <td class="num">${x.attended}/${x.booked}
        <div class="bar"><i style="width:${pct}%;background:${esc(x.colour)}"></i></div></td>
    </tr>`;
  };

  view.innerHTML = `
    <div class="head"><div>
      <h1>Dashboard</h1>
      <div class="sub">${new Date().toLocaleDateString([], {weekday:'long',day:'numeric',month:'long',year:'numeric'})}</div>
    </div>
    <div class="row">
      <a class="btn" href="/reception" target="_blank">Open reception screen</a>
      <button class="pri" onclick="newClient()">New client</button>
    </div></div>

    <div class="grid g4">
      <div class="box kpi"><div class="k">ARRIVED</div>
        <div class="v" style="color:var(--ok)">${s.exp_arrived}</div>
        <div class="n">of ${s.exp_expected} expected today</div></div>
      <div class="box kpi"><div class="k">STILL TO ARRIVE</div>
        <div class="v" style="color:${s.exp_still_due?'var(--warn)':'var(--ink)'}">${s.exp_still_due}</div>
        <div class="n">${s.exp_absent?`${s.exp_absent} marked absent`:'none absent'}</div></div>
      <div class="box kpi"><div class="k">SESSIONS TODAY</div><div class="v">${d.today_sessions.length}</div>
        <div class="n">${s.sessions_week} this week</div></div>
      <div class="box kpi"><div class="k">NEED ATTENTION</div>
        <div class="v" style="color:${d.attention.length?'var(--warn)':'var(--ok)'}">${d.attention.length}</div>
        <div class="n">low, expiring or unassigned</div></div>
    </div>

    <div class="grid g2" style="margin-top:15px">
      <div class="box kpi"><div class="k">NEW CLIENTS IN ${esc(monthName(s.mo_month).toUpperCase())}</div>
        <div class="v" style="color:var(--brand)">${s.mo_new_clients}</div>
        <div class="n">${s.mo_new_clients_prev} the month before${
          s.mo_new_plans?` · ${s.mo_new_plans} plan${s.mo_new_plans===1?'':'s'} bought`:''}</div></div>
      <div class="box kpi"><div class="k">EARNED FROM THEM</div>
        <div class="v" style="font-size:22px;padding-top:6px;color:var(--brand-deep)">${s.mo_new_revenue.toLocaleString()} <span style="font-size:12px;color:var(--mute)">EGP</span></div>
        <div class="n">${s.mo_revenue.toLocaleString()} from every plan sold this month${
          s.mo_unpriced?` · <span style="color:var(--warn)">${s.mo_unpriced} with no price on the sheet</span>`:''}</div></div>
    </div>

    <h2>Today's schedule</h2>
    <div class="box pad0">
      ${d.today_sessions.length ? `<table>
        <thead><tr><th>TIME</th><th>CLASS</th><th class="hide-sm">INSTRUCTOR</th><th>ATTENDED</th></tr></thead>
        <tbody>${d.today_sessions.map(sessionRow).join('')}</tbody></table>`
      : '<div class="empty">No sessions scheduled today.</div>'}
    </div>

    <div class="grid g2" style="margin-top:24px">
      <div>
        <h2>Needs attention</h2>
        <div class="box pad0">
          ${d.attention.length ? `<table>
            <thead><tr><th>CLIENT</th><th>MOBILE</th><th>STATUS</th></tr></thead>
            <tbody>${d.attention.map(a=>`<tr class="click" onclick="location.hash='#/client/${a.id}'">
              <td>${esc(a.name_en)}</td><td class="mute num">${esc(a.phone||'—')}</td>
              <td>${a.unassigned>0
                    ? `<span class="pill warn">${a.unassigned} unassigned</span>`
                    : balancePill(a)}</td></tr>`).join('')}</tbody></table>`
          : '<div class="empty">Everyone is in good standing.</div>'}
        </div>
      </div>
      <div>
        <h2>Recent activity</h2>
        <div class="box pad0">
          ${d.recent.length ? `<table>
            <thead><tr><th>TIME</th><th>CLIENT</th><th>RESULT</th></tr></thead>
            <tbody>${d.recent.slice(0,12).map(r=>`<tr>
              <td class="num mute" data-sort="${r.scanned_at}">${fmtTime(r.scanned_at)}</td>
              <td>${esc(r.name_en||'—')}<div class="sub">${esc(r.class_name||r.reason||'')}</div></td>
              <td>${r.confirmed_at?'<span class="pill ok">in</span>'
                   :(r.decision==='allow'?'<span class="pill grey">not confirmed</span>'
                                         :'<span class="pill bad">denied</span>')}</td>
            </tr>`).join('')}</tbody></table>`
          : '<div class="empty">No scans yet today.</div>'}
        </div>
      </div>
    </div>`;
});

/* ============================================================ CALENDAR
   The week runs Saturday to Friday, which is how the academy's timetable is
   read locally. Everything below derives from WEEK_START rather than assuming
   Monday, so changing it here is enough. */
const WEEK_START = 6;                 // 0=Sun 1=Mon ... 6=Sat
const ROW_PX = 46;
let calAnchor = null;                 // Date inside the period being viewed
let calMode = 'auto';                 // 'auto' | 'week' | 'month' | 'agenda'

function weekStartOf(d){
  const x = new Date(d); x.setHours(0,0,0,0);
  const diff = (x.getDay() - WEEK_START + 7) % 7;
  x.setDate(x.getDate() - diff);
  return x;
}

route('/calendar', async () => {
  if(!calAnchor) calAnchor = new Date();
  const narrow = window.innerWidth < 760;
  const mode = calMode === 'auto' ? (narrow ? 'agenda' : 'week') : calMode;

  let from, to, days;
  if(mode === 'month'){
    const first = new Date(calAnchor.getFullYear(), calAnchor.getMonth(), 1);
    const last  = new Date(calAnchor.getFullYear(), calAnchor.getMonth()+1, 0);
    from = weekStartOf(first);
    to = new Date(weekStartOf(last)); to.setDate(to.getDate()+7);
  } else {
    from = weekStartOf(calAnchor);
    to = new Date(from); to.setDate(to.getDate()+7);
  }
  const start = Math.floor(from.getTime()/1000);
  const end = Math.floor(to.getTime()/1000);

  const [sessions, classes] = await Promise.all([
    api(`/sessions?start=${start}&end=${end}`), api('/classes')
  ]);

  const nDays = Math.round((to - from) / 86400000);
  days = [...Array(nDays)].map((_,i)=>{ const d=new Date(from); d.setDate(d.getDate()+i); return d; });
  const todayStr = new Date().toDateString();

  const monthName = calAnchor.toLocaleDateString([], {month:'long', year:'numeric'});
  const label = mode === 'month' ? monthName
    : `${days[0].toLocaleDateString([], {day:'numeric',month:'short'})} – ${days[6].toLocaleDateString([], {day:'numeric',month:'short',year:'numeric'})}`;

  const years = [];
  const thisYear = new Date().getFullYear();
  for(let y = thisYear-2; y <= thisYear+2; y++) years.push(y);
  const months = [...Array(12)].map((_,m)=>
    new Date(2000, m, 1).toLocaleDateString([], {month:'long'}));

  view.innerHTML = `
    <div class="head"><div>
      <h1>Calendar</h1>
      <div class="sub">${label} · ${sessions.length} session${sessions.length===1?'':'s'}</div>
    </div>
    <div class="row">
      <button class="pri" onclick="newSession()">Add session</button>
      <button onclick="repeatSessions()">Repeat weekly</button>
    </div></div>

    <div class="row" style="margin-bottom:16px;gap:8px">
      <button onclick="calShift(-1)" aria-label="Previous">‹</button>
      <button onclick="calToday()">Today</button>
      <button onclick="calShift(1)" aria-label="Next">›</button>

      <select id="calMonth" style="width:auto;min-width:130px" onchange="calJump()">
        ${months.map((m,i)=>`<option value="${i}" ${i===calAnchor.getMonth()?'selected':''}>${m}</option>`).join('')}
      </select>
      <select id="calYear" style="width:auto;min-width:90px" onchange="calJump()">
        ${years.map(y=>`<option value="${y}" ${y===calAnchor.getFullYear()?'selected':''}>${y}</option>`).join('')}
      </select>

      <div style="flex:1"></div>
      <div class="row tight">
        <button class="${mode==='week'?'pri':''}" onclick="calSetMode('week')">Week</button>
        <button class="${mode==='month'?'pri':''}" onclick="calSetMode('month')">Month</button>
        <button class="${mode==='agenda'?'pri':''}" onclick="calSetMode('agenda')">List</button>
      </div>
    </div>

    <div id="calHost"></div>

    <div class="row" style="margin-top:16px">
      ${classes.map(c=>`<span class="pill grey"><span class="dot" style="background:${esc(c.colour)};width:8px;height:8px;margin:0"></span>${esc(c.name)}</span>`).join('')}
    </div>`;

  const host = $('#calHost');
  if(mode === 'agenda'){
    host.innerHTML = agendaHTML(days, sessions, todayStr);
  } else if(mode === 'month'){
    host.innerHTML = monthHTML(days, sessions, todayStr, calAnchor.getMonth());
  } else {
    let lo = 8, hi = 21;
    for(const s of sessions){
      const d = new Date(s.starts_at*1000);
      lo = Math.min(lo, d.getHours());
      hi = Math.max(hi, Math.ceil(d.getHours() + (d.getMinutes()/60) + s.duration_hours));
    }
    lo = Math.max(0, lo-1); hi = Math.min(24, hi+1);
    const hours = []; for(let h=lo; h<hi; h++) hours.push(h);
    host.innerHTML = gridHTML(days, sessions, hours, todayStr, lo);
    placeNowLine(days, lo, hi);
  }
});

function gridHTML(days, sessions, hours, todayStr, lo){
  const head = '<div class="corner"></div>' + days.map(d=>`
    <div class="hd${d.toDateString()===todayStr?' today':''}">
      <div class="d">${d.toLocaleDateString([], {weekday:'short'}).toUpperCase()}</div>
      <div class="n">${d.getDate()}</div></div>`).join('');

  const hourCol = `<div class="hourcol">${hours.map(h=>
    `<div class="hr">${String(h).padStart(2,'0')}:00</div>`).join('')}</div>`;

  const cols = days.map((day,i)=>{
    const dayStart = day.getTime()/1000, dayEnd = dayStart + 86400;
    const mine = sessions.filter(s => s.starts_at >= dayStart && s.starts_at < dayEnd);
    const slots = hours.map(h=>{
      const cell = new Date(day); cell.setHours(h,0,0,0);
      return `<div class="slot" onclick="newSession(${Math.floor(cell.getTime()/1000)})"></div>`;
    }).join('');
    const lanes = assignLanes(mine);
    const evs = mine.map((s,idx)=>{
      const d = new Date(s.starts_at*1000);
      const top = ((d.getHours()-lo) + d.getMinutes()/60) * ROW_PX;
      const h = Math.max(20, s.duration_hours*ROW_PX - 2);
      const {lane, of} = lanes[idx];
      const w = 100/of;
      return `<div class="ev ${s.status!=='scheduled'?'cancelled':''}"
        style="top:${top}px;height:${h}px;left:calc(${lane*w}% + 2px);width:calc(${w}% - 4px);
               background:${esc(s.colour)}22;border-left-color:${esc(s.colour)}"
        onclick="event.stopPropagation();location.hash='#/session/${s.id}'"
        title="${esc(s.class_name)} · ${fmtTime(s.starts_at)}">
        <div class="t">${fmtTime(s.starts_at)}</div>
        <div class="nm">${esc(s.class_name)}</div>
        ${h>52?`<div class="t">${s.attended}/${s.booked} in</div>`:''}
      </div>`;
    }).join('');
    return `<div class="daycol${day.toDateString()===todayStr?' today':''}" data-day="${i}">
      ${slots}${evs}</div>`;
  }).join('');

  return `<div class="calwrap" style="--row:${ROW_PX}px">
      <div class="calhead">${head}</div>
      <div class="calbody">${hourCol}${cols}</div>
    </div>
    <div class="sub" style="margin-top:10px">Tap an empty slot to schedule a class there.</div>`;
}

function monthHTML(days, sessions, todayStr, month){
  const names = [...Array(7)].map((_,i)=>{
    const d = new Date(2024,0,7+((WEEK_START+i)%7));
    return d.toLocaleDateString([], {weekday:'short'}).toUpperCase();
  });
  const cells = days.map(day=>{
    const dayStart = day.getTime()/1000, dayEnd = dayStart + 86400;
    const mine = sessions.filter(s => s.starts_at >= dayStart && s.starts_at < dayEnd)
                         .sort((a,b)=>a.starts_at-b.starts_at);
    const other = day.getMonth() !== month;
    return `<div class="mcell${other?' other':''}${day.toDateString()===todayStr?' today':''}"
                 onclick="newSession(${Math.floor(dayStart)+16*3600})">
      <div class="mnum">${day.getDate()}</div>
      ${mine.slice(0,4).map(s=>`<div class="mev ${s.status!=='scheduled'?'cancelled':''}"
        style="background:${esc(s.colour)}22;border-left:3px solid ${esc(s.colour)}"
        onclick="event.stopPropagation();location.hash='#/session/${s.id}'"
        title="${esc(s.class_name)} ${fmtTime(s.starts_at)}">
        <span class="t">${fmtTime(s.starts_at)}</span> ${esc(s.class_name)}</div>`).join('')}
      ${mine.length>4?`<div class="mmore">+${mine.length-4} more</div>`:''}
    </div>`;
  }).join('');

  return `<div class="calwrap">
      <div class="monthhead">${names.map(n=>`<div>${n}</div>`).join('')}</div>
      <div class="monthgrid">${cells}</div>
    </div>
    <div class="sub" style="margin-top:10px">Tap a day to schedule a class on it.</div>`;
}

function assignLanes(list){
  const out = [], lanes = [];
  list.forEach(s => {
    const s1 = s.starts_at, s2 = s.starts_at + s.duration_hours*3600;
    let idx = lanes.findIndex(end => end <= s1);
    if(idx === -1){ lanes.push(s2); idx = lanes.length-1; }
    else lanes[idx] = s2;
    out.push({lane: idx});
  });
  const of = Math.max(1, lanes.length);
  return out.map(o => ({...o, of}));
}

function agendaHTML(days, sessions, todayStr){
  return `<div class="agenda">${days.map(day=>{
    const dayStart = day.getTime()/1000, dayEnd = dayStart + 86400;
    const mine = sessions.filter(s=>s.starts_at>=dayStart && s.starts_at<dayEnd)
                         .sort((a,b)=>a.starts_at-b.starts_at);
    if(!mine.length && days.length > 10) return '';   // month list: skip empty days
    const isToday = day.toDateString()===todayStr;
    return `<div class="day">
      <div class="dayhd${isToday?' today':''}">
        <span class="dn">${day.toLocaleDateString([], {weekday:'long'})}</span>
        <span class="dd">${day.toLocaleDateString([], {day:'numeric',month:'short'})}${isToday?' · today':''}</span>
      </div>
      ${mine.length ? mine.map(s=>`
        <div class="slotrow" onclick="location.hash='#/session/${s.id}'">
          <div class="tm">${fmtTime(s.starts_at)}</div>
          <div class="bd">
            <div class="nm"><span class="dot" style="background:${esc(s.colour)}"></span>${esc(s.class_name)}</div>
            <div class="sub">${esc(s.instructor_name||'no instructor')} · ${hrs(s.duration_hours)} · ${s.attended}/${s.booked} in</div>
          </div>
          ${s.status!=='scheduled'?`<span class="pill ${s.status==='cancelled'?'bad':'grey'}">${s.status}</span>`:''}
        </div>`).join('')
      : `<div class="empty-day">Nothing scheduled.
           <a href="#" onclick="newSession(${Math.floor(dayStart)+16*3600});return false"
              style="color:var(--brand)">Add a class</a></div>`}
    </div>`;
  }).join('')}</div>`;
}

function placeNowLine(days, lo, hi){
  const now = new Date();
  const idx = days.findIndex(d => d.toDateString() === now.toDateString());
  if(idx < 0) return;
  const h = now.getHours() + now.getMinutes()/60;
  if(h < lo || h > hi) return;
  const col = document.querySelector(`.daycol[data-day="${idx}"]`);
  if(!col) return;
  const line = document.createElement('div');
  line.className = 'nowline';
  line.style.top = ((h - lo) * ROW_PX) + 'px';
  col.appendChild(line);
  col.closest('.calwrap').scrollTop = Math.max(0, (h - lo - 2) * ROW_PX);
}

window.calShift = n => {
  const narrow = window.innerWidth < 760;
  const mode = calMode === 'auto' ? (narrow ? 'agenda' : 'week') : calMode;
  const d = new Date(calAnchor);
  if(mode === 'month') d.setMonth(d.getMonth() + n);
  else d.setDate(d.getDate() + n*7);
  calAnchor = d; render();
};
window.calToday = () => { calAnchor = new Date(); render(); };
window.calSetMode = m => { calMode = m; render(); };
window.calJump = () => {
  const m = Number($('#calMonth').value), y = Number($('#calYear').value);
  calAnchor = new Date(y, m, 1);
  if(calMode === 'auto') calMode = window.innerWidth < 760 ? 'agenda' : 'month';
  render();
};

let calResizeTimer;
window.addEventListener('resize', () => {
  if(!location.hash.startsWith('#/calendar')) return;
  clearTimeout(calResizeTimer);
  calResizeTimer = setTimeout(() => render(), 250);
});

/* ============================================================ CLASSES */
route('/classes', async () => {
  const classes = await api('/classes');
  view.innerHTML = `
    <div class="head"><div><h1>Classes</h1>
      <div class="sub">${classes.length} active</div></div>
      <button class="pri" onclick="newClass()">New class</button></div>

    ${classes.length?`<div class="grid g3">${classes.map(c=>`
      <div class="box tap" style="border-left:3px solid ${esc(c.colour)}"
           onclick="location.hash='#/class/${c.id}'">
        <div style="font-size:16px;font-weight:600">${esc(c.name)}</div>
        <div class="row" style="margin-top:14px">
          <span class="pill info">${c.students} students</span>
          <span class="pill grey">${hrs(c.duration_hours)}</span>
          ${c.level?`<span class="pill grey">${esc(c.level)}</span>`:''}
        </div>
        <div class="sub" style="margin-top:12px">${c.upcoming} upcoming session${c.upcoming===1?'':'s'}</div>
      </div>`).join('')}</div>`
    :'<div class="box"><div class="empty"><strong>No classes yet</strong>Create one to start scheduling.</div></div>'}`;
});

route('/class', async (id) => {
  const c = await api('/classes/'+id);
  const now = Date.now()/1000;
  const upcoming = c.sessions.filter(x => x.starts_at >= now && x.status === 'scheduled');

  view.innerHTML = `
    <div class="head"><div>
      <div class="eyebrow">Class</div>
      <h1><span class="dot" style="background:${esc(c.colour)};width:13px;height:13px"></span>${esc(c.name)}</h1>
      <div class="sub">${hrs(c.duration_hours)}${c.level?' · '+esc(c.level):''}</div>
    </div><div class="row">
      <button onclick="editClass(${c.id})">Edit class</button>
      <button onclick="newSession(null,${c.id})">Schedule session</button>
      <button onclick="repeatSessions(${c.id})">Repeat weekly</button>
    </div></div>

    <div class="grid g4" style="margin-bottom:20px">
      <div class="box kpi"><div class="k">STUDENTS</div><div class="v">${c.students.length}</div>
        <div class="n">with a booking</div></div>
      <div class="box kpi"><div class="k">UPCOMING</div><div class="v">${upcoming.length}</div>
        <div class="n">sessions scheduled</div></div>
      <div class="box kpi"><div class="k">HELD</div>
        <div class="v">${c.sessions.filter(x=>x.status==='completed').length}</div>
        <div class="n">sessions completed</div></div>
      <div class="box kpi"><div class="k">DURATION</div>
        <div class="v" style="font-size:20px;padding-top:8px">${hrs(c.duration_hours)}</div>
        <div class="n">default per session</div></div>
    </div>

    ${c.description?`<div class="box" style="margin-bottom:20px;color:var(--mute)">${esc(c.description)}</div>`:''}

    <h2>Sessions</h2>
    <div class="sub" style="margin-bottom:12px">Each session has its own instructor — set it when scheduling, or from the session page.</div>
    <div class="box pad0">
      ${c.sessions.length?`<table data-search="Search sessions…">
        <thead><tr><th>WHEN</th><th>INSTRUCTOR</th><th class="hide-sm">LENGTH</th>
          <th>ATTENDED</th><th>STATUS</th><th></th></tr></thead>
        <tbody>${c.sessions.map(x=>`<tr class="click" onclick="location.hash='#/session/${x.id}'">
          <td data-sort="${x.starts_at}">${fmtFull(x.starts_at)}</td>
          <td class="mute">${esc(x.instructor_name||'— none —')}</td>
          <td class="mute hide-sm">${hrs(x.duration_hours)}</td>
          <td class="num">${x.attended}/${x.booked}</td>
          <td><span class="pill ${x.status==='cancelled'?'bad':x.status==='completed'?'grey':'info'}">${x.status}</span></td>
          <td class="right" style="color:var(--dim)">›</td>
        </tr>`).join('')}</tbody></table>`
      :'<div class="empty"><strong>No sessions scheduled</strong>Use Schedule session, or Repeat weekly for a whole term.</div>'}
    </div>

    <h2>Students (${c.students.length})</h2>
    <div class="sub" style="margin-bottom:12px">Anyone with a booking in this class. Membership follows the bookings, so there is no separate enrolment list.</div>
    <div class="box pad0">
      ${c.students.length?`<table data-search="Search students…">
        <thead><tr><th></th><th>NAME</th><th class="hide-sm">MOBILE</th><th>SLOTS</th><th>ATTENDED</th></tr></thead>
        <tbody>${c.students.map(m=>`<tr class="click" onclick="location.hash='#/client/${m.id}'">
          <td style="width:54px">${avatar(m)}</td>
          <td>${esc(m.name_en)}</td>
          <td class="num hide-sm">${esc(m.phone||'—')}</td>
          <td class="num">${m.slots}</td>
          <td class="num">${m.attended||0}</td>
        </tr>`).join('')}</tbody></table>`
      :'<div class="empty">Nobody booked into this class yet.</div>'}
    </div>

    <div class="row" style="margin-top:22px">
      <button class="danger" onclick="archiveClass(${c.id},'${esc(c.name)}')">Archive class</button>
    </div>`;
});

const classForm = (c={}) => `
  <label>NAME</label><input id="c_n" value="${esc(c.name||'')}" placeholder="Ballet">
  <label>DESCRIPTION</label><textarea id="c_d">${esc(c.description||'')}</textarea>
  <div class="fieldrow">
    <div><label>DEFAULT LENGTH (HOURS)</label>
      <input id="c_dur" type="number" step="0.25" min="0.25" value="${c.duration_hours||1.5}"></div>
    <div><label>LEVEL</label><input id="c_l" value="${esc(c.level||'')}" placeholder="All levels"></div>
  </div>
  <label>COLOUR</label><input id="c_c" type="color" value="${c.colour||'#87438E'}">`;

window.newClass = () => openModal(`<h3>New class</h3>
  <div class="mh">Sessions are scheduled separately once the class exists.</div>
  ${classForm()}<div class="acts"><button onclick="closeModal()">Cancel</button>
  <button class="pri" onclick="saveClass()">Create</button></div>`);

window.saveClass = async () => {
  if(!val('c_n')) return toast('Name is required','bad');
  await api('/classes', {method:'POST', body:{name:val('c_n'),
    description:val('c_d'), colour:val('c_c'),
    duration_hours:Number(val('c_dur')), level:val('c_l')}});
  closeModal(); toast('Class created'); render();
};

window.editClass = async (id) => {
  const c = await api('/classes/'+id);
  openModal(`<h3>Edit class</h3>${classForm(c)}
    <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="pri" onclick="updateClass(${id})">Save</button></div>`);
};

window.updateClass = async (id) => {
  await api('/classes/'+id, {method:'PUT', body:{name:val('c_n'),
    description:val('c_d'), colour:val('c_c'),
    duration_hours:Number(val('c_dur')), level:val('c_l')}});
  closeModal(); toast('Saved'); render();
};

window.archiveClass = (id, name) => confirmBox('Archive class',
  `<b>${esc(name)}</b> will be hidden from the list. Its sessions and every
   attendance record are kept.`,
  'Archive', async () => {
    await api('/classes/'+id, {method:'DELETE'});
    toast('Class archived'); location.hash = '#/classes'; });

/* ============================================================ SESSIONS */
route('/sessions', async () => {
  const now = Math.floor(Date.now()/1000);
  const list = await api(`/sessions?start=${now-21*86400}&end=${now+42*86400}`);
  const upcoming = list.filter(s=>s.starts_at >= now - 3600);
  const past = list.filter(s=>s.starts_at < now - 3600).reverse();

  const tbl = arr => `<table data-search="Search by class, instructor or status…">
    <thead><tr><th>WHEN</th><th>CLASS</th><th>INSTRUCTOR</th><th class="hide-sm">LENGTH</th>
      <th>ATTENDED</th><th>STATUS</th></tr></thead>
    <tbody>${arr.map(s=>`<tr class="click" onclick="location.hash='#/session/${s.id}'">
      <td data-sort="${s.starts_at}">${fmtFull(s.starts_at)}</td>
      <td><span class="dot" style="background:${esc(s.colour)}"></span>${esc(s.class_name)}</td>
      <td class="mute">${esc(s.instructor_name||'— none —')}</td>
      <td class="mute hide-sm">${hrs(s.duration_hours)}</td>
      <td class="num">${s.attended}/${s.booked}</td>
      <td><span class="pill ${s.status==='cancelled'?'bad':s.status==='completed'?'grey':'info'}">${s.status}</span></td>
    </tr>`).join('')}</tbody></table>`;

  view.innerHTML = `
    <div class="head"><div><h1>Sessions</h1>
      <div class="sub">${upcoming.length} upcoming</div></div>
      <div class="row"><button onclick="repeatSessions()">Repeat weekly</button>
      <button class="pri" onclick="newSession()">Add session</button></div></div>

    <h2>Upcoming</h2>
    <div class="box pad0">${upcoming.length?tbl(upcoming):'<div class="empty">Nothing scheduled.</div>'}</div>

    <h2>Past three weeks</h2>
    <div class="box pad0">${past.length?tbl(past):'<div class="empty">No past sessions.</div>'}</div>`;
});

route('/session', async (id) => {
  const s = await api('/sessions/'+id);
  const now = Date.now()/1000;
  const ended = s.starts_at + s.duration_hours*3600 < now;
  const present = s.roster.filter(m => m.status === 'present').length;
  const absent  = s.roster.filter(m => m.status === 'absent').length;
  const pending = s.roster.filter(m => m.status === 'booked').length;

  view.innerHTML = `
    <div class="head"><div>
      <div class="eyebrow">Session</div>
      <h1><span class="dot" style="background:${esc(s.colour)};width:13px;height:13px"></span>${esc(s.class_name)}</h1>
      <div class="sub">${fmtFull(s.starts_at)} · ${hrs(s.duration_hours)} ·
        <a href="#/class/${s.class_id}" style="color:var(--brand-deep)">view class</a></div>
    </div><div class="row">
      <button onclick="editSession(${s.id})">Edit session</button>
      ${s.status!=='cancelled'
        ? `<button class="danger" onclick="cancelSession(${s.id},'${esc(s.class_name)}')">Cancel</button>`
        : `<button onclick="setSessionStatus(${s.id},'scheduled')">Reopen</button>`}
      <button class="danger" onclick="deleteSession(${s.id})">Delete</button>
    </div></div>

    ${ended && pending ? `<div class="warnline">
      This session has finished and ${pending} ${pending===1?'person is':'people are'} still
      unmarked. They will be counted absent automatically.</div>` : ''}

    <div class="grid g4" style="margin-bottom:20px">
      <div class="box kpi ${s.instructor_id?'tap':''}"
           ${s.instructor_id?`onclick="location.hash='#/instructor/${s.instructor_id}'"`:''}>
        <div class="k">INSTRUCTOR</div>
        <div class="v" style="font-size:18px;padding-top:8px">${esc(s.instructor_name||'— none —')}</div>
        <div class="n">${s.instructor_id?'view their schedule ›':'assign one from Edit session'}</div></div>
      <div class="box kpi"><div class="k">PRESENT</div>
        <div class="v" style="color:var(--ok)">${present}</div>
        <div class="n">of ${s.roster.length} booked</div></div>
      <div class="box kpi"><div class="k">ABSENT</div>
        <div class="v" style="color:${absent?'var(--bad)':'var(--ink)'}">${absent}</div>
        <div class="n">${pending?`${pending} still to mark`:'all marked'}</div></div>
      <div class="box kpi"><div class="k">LENGTH</div>
        <div class="v" style="font-size:20px;padding-top:8px">${hrs(s.duration_hours)}</div>
        <div class="n">${s.status}</div></div>
    </div>

    <h2>Who is booked in</h2>
    <div class="sub" style="margin-bottom:12px">
      Present and absent both use the client's slot — the place was reserved either way.</div>

    <div class="row" style="margin-bottom:12px">
      <button class="pri" onclick="bookIntoSession(${s.id})">Add student</button>
    </div>

    <div class="box pad0">
      ${s.roster.length?`<table data-search="Search students…">
        <thead><tr><th></th><th>NAME</th><th class="hide-sm">MOBILE</th><th>STATUS</th>
          <th class="hide-sm">CHECKED IN</th><th>MARK AS</th><th></th></tr></thead>
        <tbody>${s.roster.map(m=>`<tr>
          <td style="width:54px">${avatar(m)}</td>
          <td class="click" onclick="location.hash='#/client/${m.id}'">${esc(m.name_en)}</td>
          <td class="num mute hide-sm">${esc(m.phone||'—')}</td>
          <td>${statusPill(m.status)}</td>
          <td class="num mute hide-sm">${m.checked_in_at?fmtTime(m.checked_in_at):'—'}</td>
          <td><div class="row tight">
            <button class="sm ${m.status==='present'?'pri':''}"
              onclick="markAs(${s.id},${m.id},'present')">Present</button>
            <button class="sm ${m.status==='absent'?'danger':''}"
              onclick="markAs(${s.id},${m.id},'absent')">Absent</button>
          </div></td>
          <td class="right"><button class="sm ghost" title="Remove from this session"
            onclick="unbookFromSession(${s.id},${m.id},'${esc(m.name_en)}')">✕</button></td>
        </tr>`).join('')}</tbody></table>`
      :'<div class="empty"><strong>Nobody booked in</strong>Add a student, or assign this session from a client\'s plan.</div>'}
    </div>`;
});

window.markAs = async (sid, cid, status) => {
  try{
    await api(`/sessions/${sid}/status-of`, {method:'POST', body:{client_id:cid, status}});
    toast(status === 'present' ? 'Marked present' : 'Marked absent');
    render();
  }catch(e){ toast(e.message,'bad'); }
};

window.bookIntoSession = async (sid) => {
  const [s, all] = await Promise.all([api('/sessions/'+sid), api('/clients')]);
  const inSession = new Set(s.roster.map(m => m.id));
  
  window._clientOptions = all.filter(c => !inSession.has(c.id));
  window._selectedClients = new Map(); // Store chosen clients: id -> client object
  
  if(!window._clientOptions.length) return toast('Every client is already booked in','bad');
  
  openModal(`<h3>Add students</h3>
    <div class="mh">Uses one slot from each selected student's plan.</div>
    
    <div id="selected_pills" class="chipbox">
      <span class="none">No students selected yet</span>
    </div>

    <label>SEARCH CLIENT</label>
    <input type="text" id="bk_search" placeholder="Type name or phone to search…"
           oninput="filterclientOptions()" style="margin-bottom:8px">

    <div id="bk_dropdown" class="picklist"></div>

    <div class="acts">
      <button onclick="closeModal()">Cancel</button>
      <button class="pri" id="bk_save_btn" onclick="saveBookIn(${sid})">Add Selected</button>
    </div>`);

  filterclientOptions();
  const searchInput = $('#bk_search');
  if(searchInput) searchInput.focus();
};

window.filterclientOptions = () => {
  const query = val('bk_search').toLowerCase();
  const container = $('#bk_dropdown');
  if(!container || !window._clientOptions) return;

  // Exclude clients that have already been added as pills
  const filtered = window._clientOptions.filter(c => {
    if (window._selectedClients.has(c.id)) return false;
    const nameMatch = (c.name_en || '').toLowerCase().includes(query);
    const phoneMatch = (c.phone || '').toLowerCase().includes(query);
    return nameMatch || phoneMatch;
  });

  if (!filtered.length) {
    container.innerHTML = '<div class="dt-none">No matching clients available</div>';
    return;
  }

  container.innerHTML = filtered.map(c => `
    <div class="pickrow" onclick="selectClient(${c.id})">
      <span style="flex:1"><b>${esc(c.name_en)}</b>${c.phone ? ' — ' + esc(c.phone) : ''}</span>
      <span class="pk-meta">${c.remaining ?? 'no plan'} left</span>
    </div>`).join('');
};

window.selectClient = (id) => {
  const client = window._clientOptions.find(c => c.id === id);
  if (!client) return;

  // Add to selection map
  window._selectedClients.set(id, client);
  
  // Clear search input so user can search again immediately
  const searchInput = $('#bk_search');
  if (searchInput) {
    searchInput.value = '';
    searchInput.focus();
  }

  renderPills();
  filterclientOptions();
};

window.removeClient = (id) => {
  window._selectedClients.delete(id);
  renderPills();
  filterclientOptions();
};

window.renderPills = () => {
  const container = $('#selected_pills');
  if (!container) return;

  if (window._selectedClients.size === 0) {
    container.innerHTML = '<span class="none">No students selected yet</span>';
    return;
  }

  container.innerHTML = Array.from(window._selectedClients.values()).map(c => `
    <span class="chip">${esc(c.name_en)}
      <b onclick="removeClient(${c.id})" title="Remove">&times;</b></span>`).join('');
};

window.saveBookIn = async (sid) => {
  const selectedIds = Array.from(window._selectedClients.keys());

  if (!selectedIds.length) {
    return toast('Please select at least one client', 'bad');
  }

  const btn = $('#bk_save_btn');
  if (btn) btn.disabled = true;

  try {
    const results = await Promise.allSettled(
      selectedIds.map(id => api(`/sessions/${sid}/book`, { method: 'POST', body: { client_id: id } }))
    );

    const failures = results.filter(r => r.status === 'rejected');
    const successes = results.filter(r => r.status === 'fulfilled');

    if (failures.length === 0) {
      toast(`Successfully booked ${successes.length} student(s)`);
    } else if (successes.length > 0) {
      toast(`Booked ${successes.length} student(s), but ${failures.length} failed`, 'bad');
    } else {
      toast(failures[0].reason?.message || 'Failed to book clients', 'bad');
    }

    closeModal();
    delete window._clientOptions;
    delete window._selectedClients;
    render();
  } catch(e) {
    toast(e.message, 'bad');
    if (btn) btn.disabled = false;
  }
};

window.unbookFromSession = (sid, cid, name) => confirmBox('Remove from this session',
  `<b>${esc(name)}</b> comes off this session and the slot returns to their plan.`,
  'Remove', async () => {
    await api(`/sessions/${sid}/book/${cid}`, {method:'DELETE'});
    toast('Removed'); render(); });

function localInput(ts){
  const d = ts ? new Date(ts*1000) : new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0,16);
}

window.newSession = async (ts=null, classId=null) => {
  const [classes, instructors] = await Promise.all([api('/classes'), api('/instructors')]);
  if(!classes.length) return toast('Create a class first','bad');
  const def = classes.find(c=>c.id==classId) || classes[0];
  openModal(`<h3>Schedule a session</h3>
    <label>CLASS</label>
    <select id="s_c" onchange="syncDuration()">${classes.map(c=>
      `<option value="${c.id}" data-dur="${c.duration_hours}" ${classId==c.id?'selected':''}>${esc(c.name)}</option>`).join('')}</select>
    <label>INSTRUCTOR</label>
    <select id="s_i"><option value="">— none yet —</option>
      ${instructors.map(i=>`<option value="${i.id}">${esc(i.name)}${i.specialty?' — '+esc(i.specialty):''}</option>`).join('')}
    </select>
    <div class="fieldrow">
      <div><label>DATE &amp; TIME</label><input id="s_t" type="datetime-local" value="${localInput(ts)}"></div>
      <div><label>LENGTH (HOURS)</label>
        <input id="s_d" type="number" step="0.25" min="0.25" value="${def.duration_hours}"></div>
    </div>
    <label>NOTES</label><input id="s_n">
    <div class="acts"><button onclick="closeModal()">Cancel</button>
      <button class="pri" onclick="saveSession()">Schedule</button></div>`);
};

window.syncDuration = () => {
  const opt = $('#s_c').selectedOptions[0];
  if(opt && $('#s_d')) $('#s_d').value = opt.dataset.dur;
};

window.saveSession = async () => {
  if(!val('s_t')) return toast('Pick a date and time','bad');
  await api('/sessions', {method:'POST', body:{
    class_id:Number($('#s_c').value),
    instructor_id:numval('s_i'),
    starts_at:Math.floor(new Date($('#s_t').value).getTime()/1000),
    duration_hours:Number(val('s_d')), notes:val('s_n')}});
  closeModal(); toast('Session scheduled'); render();
};

window.editSession = async (sid) => {
  const [s, classes, instructors] = await Promise.all([
    api('/sessions/'+sid), api('/classes'), api('/instructors')]);
  openModal(`<h3>Edit session</h3>
    <div class="mh">Moving a session does not notify anyone — tell the class yourself.</div>
    <label>CLASS</label>
    <select id="e_class">${classes.map(c=>
      `<option value="${c.id}" ${c.id===s.class_id?'selected':''}>${esc(c.name)}</option>`).join('')}</select>
    <label>INSTRUCTOR</label>
    <select id="e_ins"><option value="">— none —</option>
      ${instructors.map(i=>`<option value="${i.id}" ${i.id===s.instructor_id?'selected':''}>${esc(i.name)}</option>`).join('')}
    </select>
    <div class="fieldrow">
      <div><label>DATE &amp; TIME</label>
        <input id="e_when" type="datetime-local" value="${localInput(s.starts_at)}"></div>
      <div><label>LENGTH (HOURS)</label>
        <input id="e_dur" type="number" step="0.25" min="0.25" value="${s.duration_hours}"></div>
    </div>
    <label>NOTES</label><input id="e_notes" value="${esc(s.notes||'')}">
    <div class="acts"><button onclick="closeModal()">Cancel</button>
      <button class="pri" onclick="saveSessionEdit(${sid})">Save changes</button></div>`);
};

window.saveSessionEdit = async (sid) => {
  const when = $('#e_when').value;
  if(!when) return toast('Pick a date and time','bad');
  const body = {
    class_id: Number($('#e_class').value),
    starts_at: Math.floor(new Date(when).getTime()/1000),
    duration_hours: Number(val('e_dur')),
    notes: val('e_notes')};
  const ins = numval('e_ins');
  if(ins !== null) body.instructor_id = ins;
  try{
    // Moving a session into the future makes it scheduled again, so a date
    // corrected after the fact stops being reported as already completed.
    if (new Date(when) > new Date()) body.status = 'scheduled';
    await api('/sessions/'+sid, {method:'PUT', body});
    if(ins === null) await api('/sessions/'+sid, {method:'PUT', body:{instructor_id:null}});
    closeModal(); toast('Session updated'); render();
  }catch(e){ toast(e.message,'bad'); }
};

window.setSessionStatus = async (id, st) => {
  await api(`/sessions/${id}/status/${st}`, {method:'PUT'});
  toast('Session '+st); render();
};

window.cancelSession = (sid, name) => confirmBox('Cancel this session',
  `<b>${esc(name)}</b> will be marked cancelled and every booked slot returns to
   the clients, so nobody pays for a class that did not run.`,
  'Cancel the session', async () => {
    const r = await api(`/sessions/${sid}/cancel`, {method:'POST'});
    toast(`Cancelled — ${r.released} slot(s) returned`); render(); });

window.deleteSession = async (sid) => {
  const s = await api('/sessions/'+sid);
  const marked = s.roster.filter(m => m.status !== 'booked').length;
  if(!marked){
    return confirmBox('Delete this session',
      `<b>${esc(s.class_name)}</b> on ${fmtFull(s.starts_at)} will be removed and
       ${s.roster.length} booked slot(s) returned. Nobody has been marked yet.`,
      'Delete', async () => {
        await api('/sessions/'+sid, {method:'DELETE'});
        toast('Session deleted'); location.hash = '#/sessions'; });
  }
  openModal(`<h3>Delete a session with attendance</h3>
    <div class="mh"><b>${esc(s.class_name)}</b> has ${marked} recorded attendance
      ${marked===1?'record':'records'}.</div>
    <div class="warnline" style="margin-top:16px">
      Deleting erases who attended. Every slot returns to the clients, but the
      record that the class ran is lost.</div>
    <div class="infoline">
      <b>Cancelling</b> returns the slots too and keeps the history. That is
      usually what you want.</div>
    <div class="acts">
      <button onclick="closeModal()">Back</button>
      <button onclick="closeModal();cancelSession(${sid},'${esc(s.class_name)}')">Cancel instead</button>
      <button class="danger" onclick="forceDeleteSession(${sid})">Delete permanently</button>
    </div>`);
};

window.forceDeleteSession = async (sid) => {
  closeModal();
  try{
    const r = await api(`/sessions/${sid}?force=true`, {method:'DELETE'});
    toast(`Session deleted — ${r.released} slot(s) returned`);
    location.hash = '#/sessions';
  }catch(e){ toast(e.message,'bad'); }
};

window.repeatSessions = async (classId=null) => {
  const [classes, instructors] = await Promise.all([api('/classes'), api('/instructors')]);
  if(!classes.length) return toast('Create a class first','bad');
  const days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  openModal(`<h3>Repeat weekly</h3>
    <div class="mh">Builds a term timetable in one go. Existing sessions at the same time are skipped.</div>
    <label>CLASS</label><select id="r_c">${classes.map(c=>
      `<option value="${c.id}" data-dur="${c.duration_hours}" ${classId==c.id?'selected':''}>${esc(c.name)}</option>`).join('')}</select>
    <label>INSTRUCTOR</label>
    <select id="r_i"><option value="">— none yet —</option>
      ${instructors.map(i=>`<option value="${i.id}">${esc(i.name)}</option>`).join('')}</select>
    <div class="fieldrow">
      <div><label>FIRST SESSION</label><input id="r_t" type="datetime-local" value="${localInput()}"></div>
      <div><label>FOR HOW MANY WEEKS</label><input id="r_w" type="number" value="8" min="1" max="52"></div>
    </div>
    <label>DAYS (LEAVE EMPTY FOR THE SAME WEEKDAY)</label>
    <div class="row">${days.map((d,i)=>`<label style="display:flex;align-items:center;gap:5px;margin:0;
      letter-spacing:0;font-size:12px;color:var(--ink);text-transform:none">
      <input type="checkbox" class="rd" value="${i}" style="width:auto"> ${d}</label>`).join('')}</div>
    <div class="acts"><button onclick="closeModal()">Cancel</button>
      <button class="pri" onclick="saveRepeat()">Create sessions</button></div>`);
};

window.saveRepeat = async () => {
  const wd = [...document.querySelectorAll('.rd:checked')].map(x=>Number(x.value));
  const opt = $('#r_c').selectedOptions[0];
  const r = await api('/sessions/repeat', {method:'POST', body:{
    class_id:Number($('#r_c').value),
    instructor_id:numval('r_i'),
    starts_at:Math.floor(new Date($('#r_t').value).getTime()/1000),
    weeks:Number(val('r_w')), weekdays:wd,
    duration_hours: opt ? Number(opt.dataset.dur) : null}});
  closeModal(); toast(`${r.created} sessions created`); render();
};

/* ============================================================ INSTRUCTORS */
route('/instructors', async () => {
  const list = await api('/instructors');
  view.innerHTML = `
    <div class="head"><div><h1>Instructors</h1>
      <div class="sub">${list.length} on the team</div></div>
      <button class="pri" onclick="newInstructor()">New instructor</button></div>

    ${list.length?`<div class="grid g3">${list.map(i=>`
      <div class="box tap" onclick="location.hash='#/instructor/${i.id}'">
        <div class="row" style="gap:13px">
          <span class="avatar">${esc(initials(i.name))}</span>
          <div style="min-width:0">
            <div style="font-weight:600">${esc(i.name)}</div>
            <div class="sub">${esc(i.specialty||'—')}</div></div>
        </div>
        <div class="row" style="margin-top:14px">
          <span class="pill info">${i.sessions_taught} sessions</span>
          <span class="pill grey">${i.hours_taught} h</span>
          <span class="pill brand">${i.hourly_rate} EGP/h</span>
        </div>
        <div class="sub" style="margin-top:12px">Earned ${i.earned.toLocaleString()} EGP</div>
      </div>`).join('')}</div>`
    :'<div class="box"><div class="empty"><strong>No instructors yet</strong>Add one before scheduling sessions.</div></div>'}`;
});

route('/instructor', async (id) => {
  const i = await api('/instructors/'+id);
  const t = i.totals;
  const now = Date.now()/1000;
  const upcoming = i.sessions.filter(x => x.starts_at >= now && x.status === 'scheduled')
                             .sort((a,b) => a.starts_at - b.starts_at);
  const past = i.sessions.filter(x => x.starts_at < now);

  const table = arr => `<div class="box pad0"><table data-search="Search sessions…">
    <thead><tr><th>WHEN</th><th>CLASS</th><th class="hide-sm">LENGTH</th><th>ATTENDED</th><th>STATUS</th></tr></thead>
    <tbody>${arr.map(x=>`<tr class="click" onclick="location.hash='#/session/${x.id}'">
      <td data-sort="${x.starts_at}">${fmtFull(x.starts_at)}</td>
      <td><span class="dot" style="background:${esc(x.colour)}"></span>${esc(x.class_name)}</td>
      <td class="mute hide-sm">${hrs(x.duration_hours)}</td>
      <td class="num">${x.attended}</td>
      <td><span class="pill ${x.status==='cancelled'?'bad':x.status==='completed'?'grey':'info'}">${x.status}</span></td>
    </tr>`).join('')}</tbody></table></div>`;

  view.innerHTML = `
    <div class="head"><div class="row" style="gap:16px">
      <span class="avatar lg">${esc(initials(i.name))}</span>
      <div><div class="eyebrow">Instructor</div>
        <h1>${esc(i.name)}</h1>
        <div class="sub">${esc(i.specialty||'')}${i.phone?' · '+esc(i.phone):''}</div></div>
    </div><div class="row">
      <button onclick="editInstructor(${i.id})">Edit</button>
      <button class="danger" onclick="archiveInstructor(${i.id})">Archive</button>
    </div></div>

    <div class="grid g4" style="margin-bottom:16px">
      <div class="box kpi"><div class="k">SESSIONS TAUGHT</div>
        <div class="v">${t.sessions_taught}</div>
        <div class="n">${t.upcoming} still upcoming</div></div>
      <div class="box kpi"><div class="k">HOURS TAUGHT</div>
        <div class="v" style="color:var(--ok)">${t.hours_taught}</div>
        <div class="n">${t.upcoming_hours} h scheduled</div></div>
      <div class="box kpi"><div class="k">RATE</div>
        <div class="v" style="font-size:22px;padding-top:6px">${t.hourly_rate.toLocaleString()}</div>
        <div class="n">EGP per hour</div></div>
      <div class="box kpi"><div class="k">EARNED</div>
        <div class="v" style="font-size:22px;padding-top:6px;color:var(--brand-deep)">${t.earned.toLocaleString()}</div>
        <div class="n">EGP · ${t.upcoming_value.toLocaleString()} upcoming</div></div>
    </div>

    ${i.logged && i.logged.days ? `
    <div class="grid g2" style="margin-bottom:16px">
      <div class="box kpi"><div class="k">HOURS ON THE SALARY SHEET</div>
        <div class="v" style="color:var(--brand)">${i.logged.hours}</div>
        <div class="n">${i.logged.days} days worked · ${fmtISO(i.logged.from)} to ${fmtISO(i.logged.to)}</div></div>
      <div class="box kpi"><div class="k">PAY FOR THOSE HOURS</div>
        <div class="v" style="font-size:22px;padding-top:6px;color:var(--brand-deep)">${i.logged.pay.toLocaleString()} <span style="font-size:12px;color:var(--mute)">EGP</span></div>
        <div class="n">at ${t.hourly_rate.toLocaleString()} EGP per hour</div></div>
    </div>` : ''}

    <h2>Upcoming sessions (${upcoming.length})</h2>
    ${upcoming.length?table(upcoming)
      :'<div class="box"><div class="empty">Nothing scheduled.</div></div>'}

    <h2>Past sessions (${past.length})</h2>
    ${past.length?table(past.slice(0,40))
      :'<div class="box"><div class="empty">No sessions taught yet.</div></div>'}`;
});

const instructorForm = (i={}) => `
  <label>NAME</label><input id="i_n" value="${esc(i.name||'')}">
  <div class="fieldrow">
    <div><label>MOBILE</label><input id="i_ph" type="tel" value="${esc(i.phone||'')}"></div>
    <div><label>RATE (EGP PER HOUR)</label>
      <input id="i_rate" type="number" min="0" step="10" value="${i.hourly_rate||0}"></div>
  </div>
  <label>SPECIALTY</label><input id="i_sp" value="${esc(i.specialty||'')}" placeholder="Classical ballet">`;

window.newInstructor = () => openModal(`<h3>New instructor</h3>
  ${instructorForm()}
  <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="pri" onclick="saveInstructor()">Create</button></div>`);

window.saveInstructor = async () => {
  if(!val('i_n')) return toast('Name is required','bad');
  await api('/instructors', {method:'POST', body:{name:val('i_n'),
    phone:val('i_ph'), specialty:val('i_sp'), hourly_rate:Number(val('i_rate')||0)}});
  closeModal(); toast('Instructor added'); render();
};

window.editInstructor = async (id) => {
  const i = await api('/instructors/'+id);
  openModal(`<h3>Edit instructor</h3>${instructorForm(i)}
    <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="pri" onclick="updateInstructor(${id})">Save</button></div>`);
};

window.updateInstructor = async (id) => {
  await api('/instructors/'+id, {method:'PUT', body:{name:val('i_n'),
    phone:val('i_ph'), specialty:val('i_sp'), hourly_rate:Number(val('i_rate')||0)}});
  closeModal(); toast('Saved'); render();
};

window.archiveInstructor = (id) => confirmBox('Archive instructor',
  'They will be hidden from the list. Any upcoming session they are assigned to must be reassigned first.',
  'Archive', async () => {
    await api('/instructors/'+id, {method:'DELETE'});
    toast('Instructor archived'); location.hash = '#/instructors'; });

/* ============================================================ CLIENTS */
let clientFilter = '';
function debounce(fn, ms){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; }

route('/clients', async () => {
  const list = await api('/clients?q='+encodeURIComponent(clientFilter));
  view.innerHTML = `
    <div class="head"><div><h1>Clients</h1></div>
      <div class="row">
        <button class="pri" onclick="newClient()">New client</button>
      </div></div>

    <div class="box pad0">
      <div class="dt-bar">
        <input class="search dt-find" id="q" type="search"
               placeholder="Search name, mobile or school…" value="${esc(clientFilter)}">
        <span class="dt-count">${list.length} client${list.length===1?'':'s'}</span>
      </div>
      ${list.length?`<table>
        <thead><tr><th></th><th>NAME</th><th>MOBILE</th><th class="hide-sm">AGE</th>
          <th class="hide-sm">SCHOOL</th><th>PLAN</th><th>LEFT</th><th>CARDS</th></tr></thead>
        <tbody>${list.map(c=>`<tr class="click" onclick="location.hash='#/client/${c.id}'">
          <td style="width:54px">${avatar(c)}</td>
          <td>${esc(c.name_en)}</td>
          <td class="num">${c.phone
            ? `<a href="tel:${esc(c.phone)}" style="color:var(--brand-deep)">${esc(c.phone)}</a>`
            : '<span class="mute">—</span>'}</td>
          <td class="num mute hide-sm">${c.age ?? '—'}</td>
          <td class="mute hide-sm">${esc(c.school||'—')}</td>
          <td class="mute">${esc(c.plan||'—')}</td>
          <td>${c.frozen
                ? `<span class="pill info">frozen${c.frozen_until?' to '+esc(c.frozen_until):''}</span>`
                : c.unassigned>0
                ? `<span class="pill warn">${c.unassigned} unassigned</span>`
                : balancePill(c)}</td>
          <td>${c.cards?`<span class="pill ok">${c.cards}</span>`:'<span class="pill warn">none</span>'}</td>
        </tr>`).join('')}</tbody></table>`
      :'<div class="empty">No clients found.</div>'}
    </div>`;

  const q = $('#q');
  q.addEventListener('input', debounce(()=>{ clientFilter = q.value; render()
    .then(()=>{ const n=$('#q'); if(n){n.focus(); n.setSelectionRange(n.value.length,n.value.length);} }); }, 250));
});

route('/client', async (id) => {
  const [c, classes] = await Promise.all([api('/clients/'+id), api('/classes')]);
  window._client = c;
  window._allClasses = classes;

  /* The profile is organised by class, because that is how the money works:
     one plan per class, paying for that class's sessions, proved by that
     class's card. Totals across the top are the sum of those plans. */
  const plans = c.active_plans || [];
  const cardFor = cid => c.cards.find(x => x.class_id === cid);
  const totalLeft = plans.reduce((n,p)=>n+p.remaining, 0);
  const totalOf   = plans.reduce((n,p)=>n+p.sessions_total, 0);
  const attended  = plans.reduce((n,p)=>n+p.present, 0);
  const absent    = plans.reduce((n,p)=>n+p.absent, 0);

  view.innerHTML = `
    <div class="head"><div class="row" style="gap:16px">
      ${avatar(c, true)}
      <div><div class="eyebrow">MEMBER ${String(c.id).padStart(5,'0')}</div>
        <h1>${esc(c.name_en)}</h1>
        <div class="row" style="margin-top:9px;gap:8px">
          ${c.phone?`<a class="pill brand" href="tel:${esc(c.phone)}" style="text-decoration:none">${esc(c.phone)}</a>`
                   :'<span class="pill warn">no mobile number</span>'}
          ${c.age?`<span class="pill grey">${c.age} years old</span>`:''}
          ${c.school?`<span class="pill grey">${esc(c.school)}</span>`:''}
          ${c.joined_on?`<span class="pill grey">joined ${esc(c.joined_on)}</span>`:''}
        </div>
      </div>
    </div>
      <div class="row">
        <button onclick="uploadPhoto(${c.id})">Photo</button>
        <button onclick="editClient()">Edit</button>
        <button class="pri" onclick="newPlan(${c.id})">Add plan</button>
        <button class="danger" onclick="archiveClient(${c.id},'${esc(c.name_en)}')">Archive</button>
      </div></div>

    <div class="grid g4" style="margin-bottom:22px">
      <div class="box kpi"><div class="k">SESSIONS LEFT</div>
        <div class="v" style="color:${!plans.length?'var(--mute)':totalLeft<=0?'var(--bad)':totalLeft<=2?'var(--warn)':'var(--ok)'}">${plans.length?totalLeft:'—'}</div>
        <div class="n">${plans.length?`of ${totalOf} across ${plans.length} plan${plans.length===1?'':'s'}`:'no active plan'}</div></div>
      <div class="box kpi"><div class="k">CLASSES</div><div class="v">${plans.length}</div>
        <div class="n">${plans.map(p=>esc(p.class_name||'—')).join(', ')||'none'}</div></div>
      <div class="box kpi"><div class="k">ATTENDED</div>
        <div class="v" style="color:var(--ok)">${attended}</div>
        <div class="n">across active plans</div></div>
      <div class="box kpi"><div class="k">ABSENT</div>
        <div class="v" style="color:${absent?'var(--bad)':'var(--ink)'}">${absent}</div>
        <div class="n">across active plans</div></div>
    </div>

    <h2>Plans and cards</h2>
    <div class="sub" style="margin-bottom:12px">
      A plan is bought for one class and pays only for that class's sessions.
      Its card checks the client into those sessions and nothing else.</div>

    ${plans.length ? plans.map(p => {
      const card = cardFor(p.class_id);
      return `<div class="box" style="border-left:3px solid ${esc(p.class_colour||'#87438E')};margin-bottom:14px">
        <div class="row" style="justify-content:space-between;align-items:flex-start">
          <div>
            <div style="font-size:16px;font-weight:600">
              <span class="dot" style="background:${esc(p.class_colour||'#87438E')}"></span>${esc(p.class_name||'No class')}
              ${p.frozen?'<span class="pill info">frozen</span>':''}</div>
            <div class="sub">${esc(p.plan)} · ends ${esc(p.expires_on)}${p.frozen_days?` · ${p.frozen_days}d added by freezes`:''}</div>
          </div>
          <div class="row tight">
            ${p.frozen
              ? `<button class="sm" onclick="unfreezePlan(${p.id})">Unfreeze</button>`
              : (p.can_freeze
                  ? `<button class="sm" onclick="freezePlan(${p.id},'${esc(c.name_en)}','${esc(p.class_name||'')}')">Freeze</button>`
                  : `<button class="sm" disabled title="${esc(p.freeze_blocked_because)}">Freeze</button>`)}
            <button class="sm" onclick="newPlan(${c.id},${p.class_id})">Renew</button>
          </div>
        </div>

        <div class="row" style="margin-top:14px;gap:16px">
          <div><div class="eyebrow" style="margin:0">LEFT</div>
            <div style="font-size:20px;font-weight:600;color:${p.remaining<=0?'var(--bad)':p.remaining<=2?'var(--warn)':'var(--ok)'}">${p.remaining}<span class="sub"> of ${p.sessions_total}</span></div></div>
          <div><div class="eyebrow" style="margin:0">ATTENDED</div>
            <div style="font-size:20px;font-weight:600">${p.present}</div></div>
          <div><div class="eyebrow" style="margin:0">ABSENT</div>
            <div style="font-size:20px;font-weight:600">${p.absent}</div></div>
          <div style="flex:1"></div>
          <div style="text-align:right">
            <div class="eyebrow" style="margin:0">CARD</div>
            ${card ? `<div class="row tight" style="margin-top:5px">
                <a class="btn sm" href="${esc(card.card_url)}" download>Download</a>
                <button class="sm" onclick="window.open('${esc(card.card_url)}')">Print</button>
                <button class="sm" onclick="issueCard(${c.id},${p.class_id})">Reissue</button>
              </div>`
              : `<div class="row tight" style="margin-top:5px">
                <button class="sm pri" onclick="issueCard(${c.id},${p.class_id})">Issue card</button>
              </div>`}
          </div>
        </div>

        ${p.frozen ? `<div class="frozenline" style="margin:14px 0 0">
          <b>Frozen</b> since ${esc(p.frozen_on)}${
            p.frozen_until ? ` — lifts on <b>${esc(p.frozen_until)}</b>`
                           : ' — until you lift it'}.
          Scanning this card is refused and the expiry moves out by the length of the pause.
        </div>` : ''}

        ${p.unassigned > 0 && !p.frozen ? `<div class="warnline" style="margin:14px 0 0">
          ${p.unassigned} of these ${p.sessions_total} sessions ${p.unassigned===1?'has':'have'}
          no date yet.
          <a href="#" onclick="assignRemaining(${c.id},${p.id});return false"
             style="color:var(--warn);font-weight:600">Assign them now</a>.</div>` : ''}
      </div>`;
    }).join('') : `<div class="box"><div class="empty">
      <strong>No active plan</strong>Add one to book sessions and issue a card.</div></div>`}

    <div class="grid g2" style="margin-top:8px">
      <div>
        <h2>Payment history</h2>
        <div class="sub" style="margin-bottom:10px">Every plan bought. Click a row to see the sessions it paid for.</div>
        <div class="box pad0">
          ${c.plans.length?`<table>
            <thead><tr><th>CLASS</th><th>PLAN</th><th class="hide-sm">PERIOD</th><th>USED</th><th>PRICE</th></tr></thead>
            <tbody>${c.plans.map(pl=>`<tr class="click" onclick="planSessions(${c.id},${pl.id},'${esc(pl.plan)}')">
              <td><span class="dot" style="background:${esc(pl.class_colour||'#ccc')}"></span>${esc(pl.class_name||'—')}</td>
              <td>${esc(pl.plan)}${pl.active?' <span class="pill ok">active</span>':''}${
                pl.frozen?' <span class="pill info">frozen</span>':''}</td>
              <td class="mute num hide-sm">${pl.starts_on} → ${pl.expires_on}</td>
              <td class="num">${pl.used}/${pl.sessions_total}</td>
              <td class="num mute">${pl.price?pl.price.toLocaleString():'—'}</td>
            </tr>`).join('')}</tbody></table>`
          :'<div class="empty">No plans yet.</div>'}
        </div>
      </div>

      <div>
        <div class="row" style="justify-content:space-between;align-items:baseline;margin:32px 0 14px">
          <h2 style="margin:0">Upcoming sessions</h2>
          <button class="sm" onclick="addClientSession(${c.id})">Add session</button>
        </div>
        <div class="sub" style="margin-bottom:10px">Click one to move it to another date of the same class.</div>
        <div class="box pad0">
          ${c.upcoming.length?`<table>
            <thead><tr><th>WHEN</th><th>CLASS</th><th></th></tr></thead>
            <tbody>${c.upcoming.map(u=>`<tr>
              <td data-sort="${u.starts_at}">${fmtFull(u.starts_at)}<div class="sub">${esc(u.instructor_name||'no instructor')}</div></td>
              <td><span class="dot" style="background:${esc(u.colour)}"></span>${esc(u.class_name)}</td>
              <td class="right"><div class="row tight" style="justify-content:flex-end">
                <button class="sm" onclick="moveBooking(${c.id},${u.session_id},${u.class_id})">Move</button>
                <button class="sm ghost" onclick="dropBooking(${c.id},${u.session_id})">✕</button>
              </div></td>
            </tr>`).join('')}</tbody></table>`
          :'<div class="empty">No upcoming sessions booked.</div>'}
        </div>

        <h2>Attendance history</h2>
        <div class="sub" style="margin-bottom:10px">Click a row to correct whether they attended.</div>
        <div class="box pad0">
          ${c.history.length?`<table data-search="Search attendance…">
            <thead><tr><th>DATE</th><th>CLASS</th><th>RESULT</th></tr></thead>
            <tbody>${c.history.map(h=>`<tr class="click"
              onclick="editAttendance(${c.id},${h.session_id},'${esc(h.class_name)}','${h.status}',${h.starts_at})">
              <td class="num mute" data-sort="${h.starts_at}">${fmtDate(h.starts_at)} ${fmtTime(h.starts_at)}</td>
              <td><span class="dot" style="background:${esc(h.colour)}"></span>${esc(h.class_name)}</td>
              <td>${statusPill(h.status)}</td>
            </tr>`).join('')}</tbody></table>`
          :'<div class="empty">No past sessions.</div>'}
        </div>
      </div>
    </div>`;
});

/* ---------- client create / edit ---------- */
const clientForm = (c={}) => `
  <div class="fieldrow">
    <div><label>FULL NAME</label><input id="f_en" value="${esc(c.name_en||'')}" placeholder="Ahmed Hassan"></div>
    <div><label>MOBILE NUMBER</label><input id="f_ph" type="tel" inputmode="tel"
      value="${esc(c.phone||'')}" placeholder="01001234567"></div>
  </div>
  <div class="fieldrow">
    <div><label>AGE</label><input id="f_age" type="number" min="1" max="99" value="${c.age ?? ''}"></div>
    <div><label>FIRST JOINED</label><input id="f_join" type="date" value="${esc(c.joined_on||todayISO())}"></div>
  </div>
  <label>SCHOOL / UNIVERSITY</label><input id="f_school" value="${esc(c.school||'')}" placeholder="Manara Language School">
  <label>NOTES</label><textarea id="f_no">${esc(c.notes||'')}</textarea>`;

window.newClient = () => openModal(`<h3>New client</h3>
  <div class="mh">Add their plan next — that is where sessions get assigned.</div>
  ${clientForm()}
  <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="pri" onclick="saveClient()">Create</button></div>`);

window.saveClient = async () => {
  if(!val('f_en')) return toast('Name is required','bad');
  const r = await api('/clients', {method:'POST', body:{
    name_en:val('f_en'), phone:val('f_ph'), age:numval('f_age'),
    school:val('f_school'), joined_on:val('f_join'), notes:val('f_no')}});
  closeModal(); toast('Client created');
  location.hash = '#/client/'+r.id;
};

window.editClient = () => openModal(`<h3>Edit client</h3>${clientForm(window._client)}
  <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="pri" onclick="updateClient(${window._client.id})">Save</button></div>`);

window.updateClient = async (id) => {
  await api('/clients/'+id, {method:'PUT', body:{
    name_en:val('f_en'), phone:val('f_ph'), age:numval('f_age'),
    school:val('f_school'), joined_on:val('f_join'), notes:val('f_no')}});
  closeModal(); toast('Saved'); render();
};

window.uploadPhoto = (id) => {
  const inp = document.createElement('input');
  inp.type='file'; inp.accept='image/*';
  inp.onchange = async () => {
    const fd = new FormData(); fd.append('file', inp.files[0]);
    const r = await fetch(`/api/clients/${id}/photo`, {method:'POST', body:fd});
    if(r.ok){ toast('Photo saved'); render(); } else toast('Upload failed','bad');
  };
  inp.click();
};

window.archiveClient = (id, name) => confirmBox('Archive client',
  `<b>${esc(name)}</b> will be hidden and their cards revoked. Any future bookings
   are released; their attendance history is kept.`,
  'Archive', async () => {
    await api('/clients/'+id, {method:'DELETE'});
    toast('Client archived'); location.hash = '#/clients'; });

window.issueCard = async (cid, classId) => {
  try{
    const r = await api(`/clients/${cid}/card`, {method:'POST', body:{class_id:classId}});
    toast(r.revoked ? 'New card issued — old one revoked' : 'Card issued');
    render();
  }catch(e){ toast(e.message,'bad'); }
};

/* ============================================================ PLANS
   Selling a plan and scheduling it are one action. A plan whose slots are not
   assigned to real dates is a promise nobody has written down, so Save stays
   disabled until every slot has a session. */
let planPick = { need: 0, chosen: [], sessions: [], cid: null, classId: null };

window.newPlan = async (cid, presetClass=null) => {
  const classes = await api('/classes');
  if(!classes.length) return toast('Create a class first','bad');
  const chosenClass = presetClass || classes[0].id;
  planPick = { need: 12, chosen: [], sessions: [], cid, classes, classId: chosenClass };

  const end = new Date(); end.setMonth(end.getMonth()+3);
  openModal(`<h3>${presetClass?'Renew plan':'New plan'}</h3>
    <div class="mh">A plan is bought for one class and pays only for that class's
      sessions. Renewing replaces the previous plan for the same class; plans for
      other classes are untouched.</div>

    <label>CLASS</label>
    <select id="p_class" onchange="planClassChanged()">
      ${classes.map(k=>`<option value="${k.id}" ${k.id===chosenClass?'selected':''}>${esc(k.name)}</option>`).join('')}
    </select>

    <div class="fieldrow">
      <div><label>PLAN NAME</label><input id="p_name" value="12 sessions"></div>
      <div><label>NUMBER OF SESSIONS</label>
        <input id="p_n" type="number" value="12" min="1" max="200" oninput="planNeedChanged()"></div>
    </div>
    <div class="fieldrow">
      <div><label>STARTS ON</label><input id="p_start" type="date" value="${todayISO()}"></div>
      <div><label>ENDS ON</label><input id="p_end" type="date" value="${end.toISOString().slice(0,10)}"></div>
    </div>
    <label>PRICE (EGP)</label><input id="p_price" type="number" placeholder="optional">

    <div class="divider"></div>
    <div class="row" style="justify-content:space-between;align-items:baseline">
      <b style="font-size:14px">Assign the sessions</b>
      <span id="p_count" class="pill warn">0 of 12 chosen</span>
    </div>
    <div class="sub" style="margin:6px 0 10px" id="p_hint">Pick a date for every session in the plan.</div>
    <div class="row" style="margin-bottom:8px">
      <button class="sm" onclick="planAutoFill()">Auto-fill earliest</button>
      <button class="sm" onclick="planClear()">Clear</button>
    </div>
    <div id="p_sessions" class="picklist"></div>

    <div class="acts">
      <button onclick="closeModal()">Cancel</button>
      <button class="pri" id="p_save" disabled onclick="savePlan(${cid})">Save plan</button>
    </div>`, true);
  await loadPlanSessions();
};

/* Sessions are always fetched for one class. The picker cannot show a session
   the plan is not allowed to pay for, which is the same rule the server
   enforces — the UI just never offers the mistake. */
async function loadPlanSessions(){
  const now = Math.floor(Date.now()/1000);
  planPick.sessions = await api(
    `/sessions?start=${now}&end=${now+180*86400}` +
    `&class_id=${planPick.classId}&available_for=${planPick.cid}`);
  planPick.chosen = [];
  const k = (planPick.classes||[]).find(x=>x.id===planPick.classId);
  const hint = $('#p_hint');
  if(hint) hint.textContent = k
    ? `Only ${k.name} sessions are offered — a plan pays for its own class.`
    : 'Pick a date for every session in the plan.';
  renderPlanSessions();
}

window.planClassChanged = async () => {
  planPick.classId = Number($('#p_class').value);
  await loadPlanSessions();
};

window.assignRemaining = async (cid, planId) => {
  const client = await api('/clients/'+cid);
  const plan = client.plans.find(p => p.id === planId);
  const now = Math.floor(Date.now()/1000);
  const sessions = await api(
    `/sessions?start=${now}&end=${now+180*86400}&class_id=${plan.class_id}&available_for=${cid}`);
  planPick = { need: plan.unassigned, chosen: [], sessions, cid,
               classes: [], classId: plan.class_id, topUp: planId };

  openModal(`<h3>Assign remaining sessions</h3>
    <div class="mh">${esc(plan.plan)} has ${plan.unassigned} session${plan.unassigned===1?'':'s'}
      without a date. Only ${esc(plan.class_name||'that class')}'s sessions are offered.</div>
    <div class="row" style="justify-content:space-between;align-items:baseline;margin-top:14px">
      <b style="font-size:14px">Pick the dates</b>
      <span id="p_count" class="pill warn">0 of ${plan.unassigned} chosen</span>
    </div>
    <div class="row" style="margin:10px 0 8px">
      <button class="sm" onclick="planAutoFill()">Auto-fill earliest</button>
      <button class="sm" onclick="planClear()">Clear</button>
    </div>
    <div id="p_sessions" class="picklist"></div>
    <div class="acts">
      <button onclick="closeModal()">Cancel</button>
      <button class="pri" id="p_save" disabled onclick="saveTopUp(${cid},${planId})">Assign</button>
    </div>`, true);
  renderPlanSessions();
};

window.planNeedChanged = () => {
  planPick.need = Number($('#p_n').value) || 0;
  if(planPick.chosen.length > planPick.need)
    planPick.chosen = planPick.chosen.slice(0, planPick.need);
  renderPlanSessions();
};

window.renderPlanSessions = () => {
  const list = planPick.sessions;
  const host = $('#p_sessions');
  if(!host) return;

  host.innerHTML = list.length ? list.map(s => {
    const sid = s.id;
    const on = planPick.chosen.includes(sid);
    return `<label class="pickrow${on?' on':''}">
      <input type="checkbox" ${on?'checked':''} onchange="planToggle(${sid})">
      <span class="dot" style="background:${esc(s.colour)}"></span>
      <span class="pk-when">${fmtDay(s.starts_at)} · ${fmtTime(s.starts_at)}</span>
      <span class="pk-class">${esc(s.class_name)}</span>
      <span class="pk-meta">${esc(s.instructor_name||'no instructor')} · ${s.booked} booked</span>
    </label>`;
  }).join('') : '<div class="empty">No upcoming sessions in this class. Schedule some first.</div>';

  const n = planPick.chosen.length;
  const cnt = $('#p_count');
  if(cnt){
    cnt.textContent = `${n} of ${planPick.need} chosen`;
    cnt.className = 'pill ' + (n === planPick.need ? 'ok' : 'warn');
  }
  const save = $('#p_save');
  if(save) save.disabled = !(n === planPick.need && planPick.need > 0);
};

window.planToggle = (sid) => {
  const i = planPick.chosen.indexOf(sid);
  if(i >= 0) planPick.chosen.splice(i,1);
  else {
    if(planPick.chosen.length >= planPick.need){
      toast(`That is all ${planPick.need} sessions — untick one first`,'bad');
      renderPlanSessions(); return;
    }
    planPick.chosen.push(sid);
  }
  renderPlanSessions();
};

window.planAutoFill = () => {
  planPick.chosen = planPick.sessions.slice(0, planPick.need).map(s => s.id);
  renderPlanSessions();
};

window.planClear = () => { planPick.chosen = []; renderPlanSessions(); };

window.savePlan = async (cid) => {
  try{
    await api(`/clients/${cid}/plan`, {method:'POST', body:{
      class_id: planPick.classId,
      plan: val('p_name'),
      sessions_total: Number(val('p_n')),
      price: numval('p_price'),
      starts_on: val('p_start'),
      expires_on: val('p_end'),
      session_ids: planPick.chosen}});
    closeModal();
    toast('Plan saved — issue the card for this class next');
    render();
  }catch(e){ toast(e.message,'bad'); }
};

window.saveTopUp = async (cid, planId) => {
  try{
    for(const sid of planPick.chosen){
      await api(`/sessions/${sid}/book`, {method:'POST', body:{client_id:cid}});
    }
    closeModal(); toast('Sessions assigned'); render();
  }catch(e){ toast(e.message,'bad'); }
};

/* ============================================================ ADD A SESSION
   From a client's profile: book them into an upcoming session of a class they
   already hold an active plan in. This spends a slot from that plan the same
   way a scan at reception does — access.book() picks the plan for the
   session's own class, so the right balance is charged without asking here. */
let addPick = { cid: null, classId: null, classes: [], sessions: [], chosen: [] };

window.addClientSession = async (cid) => {
  const client = (window._client && window._client.id === cid) ? window._client : await api('/clients/'+cid);
  const classes = client.classes_enrolled || [];
  if(!classes.length) return toast('Add a plan before booking a session','bad');

  addPick = { cid, classId: classes[0].class_id, classes, sessions: [], chosen: [] };

  openModal(`<h3>Add a session</h3>
    <div class="mh">Only sessions of a class they hold an active plan in are offered —
      booking one spends a slot from that plan, the same as a scan.</div>

    ${classes.length > 1 ? `<label>CLASS</label>
    <select id="as_class" onchange="addSessionClassChanged()">
      ${classes.map(k=>`<option value="${k.class_id}">${esc(k.class_name)}</option>`).join('')}
    </select>` : ''}

    <div class="row" style="justify-content:space-between;align-items:baseline;margin-top:${classes.length>1?'14px':'0'}">
      <b style="font-size:14px">Choose the session(s)</b>
      <span id="as_count" class="pill warn">0 chosen</span>
    </div>
    <div id="as_sessions" class="picklist" style="margin-top:8px"></div>

    <div class="acts">
      <button onclick="closeModal()">Cancel</button>
      <button class="pri" id="as_save" disabled onclick="saveClientSession()">Add</button>
    </div>`, true);

  await loadAddSessions();
};

async function loadAddSessions(){
  const now = Math.floor(Date.now()/1000);
  addPick.sessions = await api(
    `/sessions?start=${now}&end=${now+180*86400}` +
    `&class_id=${addPick.classId}&available_for=${addPick.cid}`);
  addPick.chosen = [];
  renderAddSessions();
}

window.addSessionClassChanged = async () => {
  addPick.classId = Number($('#as_class').value);
  await loadAddSessions();
};

window.renderAddSessions = () => {
  const host = $('#as_sessions');
  if(!host) return;
  const list = addPick.sessions;

  host.innerHTML = list.length ? list.map(s => {
    const on = addPick.chosen.includes(s.id);
    return `<label class="pickrow${on?' on':''}">
      <input type="checkbox" ${on?'checked':''} onchange="addSessionToggle(${s.id})">
      <span class="dot" style="background:${esc(s.colour)}"></span>
      <span class="pk-when">${fmtDay(s.starts_at)} · ${fmtTime(s.starts_at)}</span>
      <span class="pk-class">${esc(s.class_name)}</span>
      <span class="pk-meta">${esc(s.instructor_name||'no instructor')} · ${s.booked} booked</span>
    </label>`;
  }).join('') : '<div class="empty">No upcoming sessions in this class.</div>';

  const n = addPick.chosen.length;
  const cnt = $('#as_count');
  if(cnt){ cnt.textContent = `${n} chosen`; cnt.className = 'pill ' + (n ? 'ok' : 'warn'); }
  const save = $('#as_save');
  if(save) save.disabled = n === 0;
};

window.addSessionToggle = (sid) => {
  const i = addPick.chosen.indexOf(sid);
  if(i >= 0) addPick.chosen.splice(i,1); else addPick.chosen.push(sid);
  renderAddSessions();
};

window.saveClientSession = async () => {
  const ids = addPick.chosen.slice();
  if(!ids.length) return;
  const btn = $('#as_save'); if(btn) btn.disabled = true;
  try{
    const results = await Promise.allSettled(
      ids.map(sid => api(`/sessions/${sid}/book`, {method:'POST', body:{client_id: addPick.cid}})));
    const fail = results.filter(r => r.status === 'rejected');
    const ok = results.length - fail.length;
    if(!fail.length){
      closeModal(); toast(`Added ${ok} session${ok===1?'':'s'}`); render();
    } else if(ok){
      closeModal(); toast(`Added ${ok}, ${fail.length} failed`, 'bad'); render();
    } else {
      toast(fail[0].reason?.message || 'Could not add session', 'bad');
      if(btn) btn.disabled = false;
    }
  }catch(e){ toast(e.message,'bad'); if(btn) btn.disabled = false; }
};

/* ---------- plan detail popup ---------- */
window.planSessions = async (cid, pid, name) => {
  const list = await api(`/clients/${cid}/plan/${pid}/sessions`);
  const present = list.filter(x=>x.status==='present').length;
  const absent = list.filter(x=>x.status==='absent').length;
  openModal(`<h3>${esc(name)}</h3>
    <div class="mh">Every session this payment covered.</div>
    <div class="row" style="margin:14px 0">
      <span class="pill ok">${present} attended</span>
      <span class="pill bad">${absent} absent</span>
      <span class="pill info">${list.length-present-absent} upcoming</span>
    </div>
    <div class="box pad0" style="max-height:50vh;overflow-y:auto">
      ${list.length?`<table><thead><tr><th>WHEN</th><th>CLASS</th><th>RESULT</th></tr></thead>
        <tbody>${list.map(x=>`<tr>
          <td class="num mute" data-sort="${x.starts_at}">${fmtDate(x.starts_at)} ${fmtTime(x.starts_at)}</td>
          <td><span class="dot" style="background:${esc(x.colour)}"></span>${esc(x.class_name)}</td>
          <td>${statusPill(x.status)}</td>
        </tr>`).join('')}</tbody></table>`
      :'<div class="empty">No sessions assigned to this plan.</div>'}
    </div>
    <div class="acts"><button class="pri" onclick="closeModal()">Close</button></div>`, true);
};

/* ---------- attendance correction ---------- */
window.editAttendance = (cid, sid, className, status, ts) => {
  openModal(`<h3>${esc(className)}</h3>
    <div class="mh">${fmtFull(ts)}</div>
    <div class="row" style="margin:18px 0">Currently ${statusPill(status)}</div>
    <div class="sub">Both present and absent use the client's slot. Changing this only
      corrects the record of what happened.</div>
    <div class="acts">
      <button onclick="closeModal()">Cancel</button>
      <button class="${status==='absent'?'pri':''}" onclick="fixAttendance(${cid},${sid},'absent')">Mark absent</button>
      <button class="${status==='present'?'pri':''}" onclick="fixAttendance(${cid},${sid},'present')">Mark present</button>
    </div>`);
};

window.fixAttendance = async (cid, sid, status) => {
  try{
    await api(`/sessions/${sid}/status-of`, {method:'POST', body:{client_id:cid, status}});
    closeModal(); toast(status==='present'?'Marked present':'Marked absent'); render();
  }catch(e){ toast(e.message,'bad'); }
};

/* ---------- moving an upcoming booking ---------- */
window.moveBooking = async (cid, fromSid, classId) => {
  const list = await api(`/sessions?start=${Math.floor(Date.now()/1000)}&end=${Math.floor(Date.now()/1000)+180*86400}&class_id=${classId}&available_for=${cid}`);
  if(!list.length) return toast('No other sessions of this class to move to','bad');
  openModal(`<h3>Move this session</h3>
    <div class="mh">Only sessions of the same class are offered.</div>
    <label>MOVE TO</label>
    <select id="mv_to">${list.map(s=>
      `<option value="${s.id}">${fmtFull(s.starts_at)} — ${esc(s.instructor_name||'no instructor')} (${s.booked} booked)</option>`).join('')}</select>
    <div class="acts"><button onclick="closeModal()">Cancel</button>
      <button class="pri" onclick="saveMove(${cid},${fromSid})">Move</button></div>`);
};

window.saveMove = async (cid, fromSid) => {
  try{
    await api(`/clients/${cid}/move-booking/${fromSid}`, {method:'POST',
      body:{to_session_id: Number($('#mv_to').value)}});
    closeModal(); toast('Session moved'); render();
  }catch(e){ toast(e.message,'bad'); }
};

window.dropBooking = (cid, sid) => confirmBox('Release this session',
  'The slot returns to their plan and can be assigned to another date.',
  'Release', async () => {
    await api(`/sessions/${sid}/book/${cid}`, {method:'DELETE'});
    toast('Slot released'); render(); });

/* ============================================================ CARDS & RENEWALS */
route('/cards', async () => {
  const list = await api('/clients?status=attention');
  view.innerHTML = `
    <div class="head"><div><h1>Cards &amp; renewals</h1>
      <div class="sub">Clients who need a card, a renewal, or have sessions still unassigned</div></div></div>

    <div class="box pad0">
      ${list.length?`<table data-search="Search clients…">
        <thead><tr><th></th><th>NAME</th><th>MOBILE</th><th>ISSUE</th><th>LEFT</th><th></th></tr></thead>
        <tbody>${list.map(c=>{
          let issue = '<span class="pill grey">—</span>';
          if(!c.cards) issue = '<span class="pill warn">no card</span>';
          else if(c.expired) issue = '<span class="pill bad">plan expired</span>';
          else if(c.unassigned>0) issue = `<span class="pill warn">${c.unassigned} unassigned</span>`;
          else if(c.empty) issue = '<span class="pill bad">no sessions</span>';
          else if(c.low) issue = '<span class="pill warn">running low</span>';
          return `<tr>
            <td style="width:54px">${avatar(c)}</td>
            <td class="click" onclick="location.hash='#/client/${c.id}'">${esc(c.name_en)}</td>
            <td class="mute num">${esc(c.phone||'—')}</td>
            <td>${issue}</td>
            <td>${balancePill(c)}</td>
            <td class="right"><button class="sm" onclick="location.hash='#/client/${c.id}'">Open</button></td>
          </tr>`;}).join('')}</tbody></table>`
      :'<div class="empty">Nothing needs attention.</div>'}
    </div>`;
});

render();

/* ============================================================ FREEZING
   A client goes away and asks to pause. Releasing their booked sessions is the
   part that matters: left booked, every one would be swept to absent while they
   are away and the plan would be empty on their return. */
window.freezePlan = async (planId, name, className='') => {
  const soon = new Date(); soon.setMonth(soon.getMonth()+1);
  openModal(`<h3>Freeze ${className?esc(className)+' plan':'this plan'}</h3>
    <div class="mh">${esc(name)} keeps every session they have paid for. The dates
      they are booked into during the pause are released, and the expiry moves out
      by however long the freeze lasts. Their other classes carry on as normal.</div>

    <label>HOW SHOULD IT END</label>
    <select id="fz_mode" onchange="freezeModeChanged()">
      <option value="open">When I unfreeze it</option>
      <option value="dated">Automatically on a date</option>
    </select>

    <div id="fz_dateRow" style="display:none">
      <label>FROZEN UNTIL</label>
      <input id="fz_until" type="date" value="${soon.toISOString().slice(0,10)}">
      <div class="hint">It lifts itself on this date — nobody has to remember.</div>
    </div>

    <label>REASON</label>
    <input id="fz_reason" placeholder="Travelling, injury, exams…">

    <div class="infoline" style="margin-top:16px">
      While frozen, scanning their card is refused so a session cannot be spent
      by accident. Sessions already marked present or absent are untouched.
    </div>

    <div class="acts"><button onclick="closeModal()">Cancel</button>
      <button class="pri" onclick="saveFreeze(${planId})">Freeze plan</button></div>`);
};

window.freezeModeChanged = () => {
  $('#fz_dateRow').style.display = $('#fz_mode').value === 'dated' ? 'block' : 'none';
};

window.saveFreeze = async (planId) => {
  const dated = $('#fz_mode').value === 'dated';
  try{
    const r = await api(`/plans/${planId}/freeze`, {method:'POST', body:{
      until: dated ? val('fz_until') : null,
      reason: val('fz_reason')}});
    closeModal();
    toast(r.released
      ? `Plan frozen — ${r.released} booked session${r.released===1?'':'s'} released`
      : 'Plan frozen');
    render();
  }catch(e){ toast(e.message,'bad'); }
};

window.unfreezePlan = (planId) => confirmBox('Unfreeze this plan',
  `The expiry date moves out by the length of the pause. Any sessions released
   when it was frozen come back as unassigned, so you will need to give them
   new dates.`,
  'Unfreeze', async () => {
    const r = await api(`/plans/${planId}/unfreeze`, {method:'POST'});
    toast(`Unfrozen — ${r.days} day${r.days===1?'':'s'} added, now ends ${r.expires_on}`);
    render();
  }, false);
