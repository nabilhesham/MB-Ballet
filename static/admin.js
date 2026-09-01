/* Admin console — data manager, users, activity log.
   Loaded after app.js, so it can register extra routes on the same router. */

/* ============================================================ DATA MANAGER */
route('/admin', async () => {
  const [ov, arch] = await Promise.all([api('/admin/overview'), api('/admin/archived')]);
  const c = ov.counts;

  const kpi = (k,v,n='') => `<div class="box kpi"><div class="k">${k}</div>
    <div class="v">${v}</div>${n?`<div class="n">${n}</div>`:''}</div>`;

  view.innerHTML = `
    <div class="head"><div><h1>Data manager</h1>
      <div class="sub">Everything in the database, with the operations the daily screens don't offer</div>
    </div></div>

    <div class="warnline">Changes here bypass the normal workflow and are recorded in the activity log.</div>

    <div class="grid g4">
      ${kpi('CLIENTS', c.clients, `${ov.archived.clients} archived`)}
      ${kpi('CLASSES', c.classes, `${ov.archived.classes} archived`)}
      ${kpi('SESSIONS', c.sessions)}
      ${kpi('INSTRUCTORS', c.instructors, `${ov.archived.instructors} archived`)}
    </div>
    <div class="grid g4" style="margin-top:14px">
      ${kpi('PLANS SOLD', c.subscriptions, ov.revenue.total ? `EGP ${ov.revenue.total.toLocaleString()} recorded` : 'no prices entered')}
      ${kpi('ENROLMENTS', c.enrolments)}
      ${kpi('ATTENDANCE', c.attendance)}
      ${kpi('SCANS LOGGED', c.access_events)}
    </div>

    <h2>Manage records</h2>
    <div class="tabs" id="dmTabs">
      <button class="on" onclick="dmTab('clients')">Clients</button>
      <button onclick="dmTab('classes')">Classes</button>
      <button onclick="dmTab('sessions')">Sessions</button>
      <button onclick="dmTab('instructors')">Instructors</button>
      <button onclick="dmTab('archive')">Archive</button>
    </div>
    <div id="dmBody"><div class="empty">Loading…</div></div>

    <h2 style="margin-top:34px">Housekeeping</h2>
    <div class="danger-zone">
      <h2 style="font-size:15px">Danger zone</h2>
      <div class="sub" style="margin-bottom:14px">
        Archiving is reversible and keeps history. Permanent deletion is refused for
        anything with recorded attendance.</div>
      <div class="row">
        <button onclick="dmTab('archive')">View archived records</button>
        <a class="btn" href="#/audit">Activity log</a>
      </div>
    </div>`;

  window._archived = arch;
  dmTab('clients');
});

window.dmTab = async (which) => {
  document.querySelectorAll('#dmTabs button').forEach((b,i) =>
    b.classList.toggle('on', ['clients','classes','sessions','instructors','archive'][i] === which));
  const body = $('#dmBody');
  body.innerHTML = '<div class="empty">Loading…</div>';

  if(which === 'clients'){
    const list = await api('/clients');
    body.innerHTML = `<div class="row" style="margin-bottom:12px">
        <button class="pri" onclick="newClient()">New client</button></div>
      <div class="box pad0"><table>
      <thead><tr><th>ID</th><th>NAME</th><th>PHONE</th><th>PLAN</th><th>BALANCE</th><th>ACTIONS</th></tr></thead>
      <tbody>${list.map(c=>`<tr>
        <td class="num mute">${c.id}</td>
        <td class="click" onclick="location.hash='#/client/${c.id}'">${esc(c.name_en)}</td>
        <td class="mute num">${esc(c.phone||'—')}</td>
        <td class="mute">${esc(c.plan||'—')}</td>
        <td>${balancePill(c)}</td>
        <td><div class="row" style="justify-content:flex-end">
          <button class="sm" onclick="adjustBalance(${c.id})">Adjust</button>
          <button class="sm danger" onclick="archiveClient(${c.id},'${esc(c.name_en)}')">Archive</button>
        </div></td></tr>`).join('')}</tbody></table></div>`;
  }

  if(which === 'classes'){
    const list = await api('/classes');
    body.innerHTML = `<div class="row" style="margin-bottom:12px">
        <button class="pri" onclick="newClass()">New class</button></div>
      <div class="box pad0"><table>
      <thead><tr><th>ID</th><th>NAME</th><th>INSTRUCTOR</th><th>MEMBERS</th><th>UPCOMING</th><th>ACTIONS</th></tr></thead>
      <tbody>${list.map(c=>`<tr>
        <td class="num mute">${c.id}</td>
        <td class="click" onclick="location.hash='#/class/${c.id}'">
          <span class="dot" style="background:${esc(c.colour)}"></span>${esc(c.name)}</td>
        <td class="mute">${esc(c.instructor_name||'—')}</td>
        <td class="num">${c.members}</td>
        <td class="num">${c.upcoming}</td>
        <td><div class="row" style="justify-content:flex-end">
          <button class="sm" onclick="editClass(${c.id})">Edit</button>
          <button class="sm danger" onclick="archiveClass(${c.id},'${esc(c.name)}')">Archive</button>
        </div></td></tr>`).join('')}</tbody></table></div>`;
  }

  if(which === 'sessions'){
    const now = Math.floor(Date.now()/1000);
    const list = await api(`/sessions?start=${now-30*86400}&end=${now+60*86400}`);
    body.innerHTML = `<div class="row" style="margin-bottom:12px">
        <button class="pri" onclick="newSession()">Add session</button>
        <button onclick="repeatSessions()">Repeat weekly</button></div>
      <div class="box pad0"><table>
      <thead><tr><th>ID</th><th>WHEN</th><th>CLASS</th><th>ATTENDED</th><th>STATUS</th><th>ACTIONS</th></tr></thead>
      <tbody>${list.map(s=>`<tr>
        <td class="num mute">${s.id}</td>
        <td class="click" onclick="location.hash='#/session/${s.id}'">${fmtFull(s.starts_at)}</td>
        <td><span class="dot" style="background:${esc(s.colour)}"></span>${esc(s.class_name)}</td>
        <td class="num">${s.attended}</td>
        <td><span class="pill ${s.status==='cancelled'?'bad':s.status==='completed'?'grey':'info'}">${s.status}</span></td>
        <td><div class="row" style="justify-content:flex-end">
          <button class="sm" onclick="setStatus(${s.id},'cancelled')">Cancel</button>
          <button class="sm danger" onclick="deleteSession(${s.id},${s.attended})">Delete</button>
        </div></td></tr>`).join('')}</tbody></table></div>`;
  }

  if(which === 'instructors'){
    const list = await api('/instructors');
    body.innerHTML = `<div class="row" style="margin-bottom:12px">
        <button class="pri" onclick="newInstructor()">New instructor</button></div>
      <div class="box pad0"><table>
      <thead><tr><th>ID</th><th>NAME</th><th>SPECIALTY</th><th>PHONE</th><th>CLASSES</th><th>ACTIONS</th></tr></thead>
      <tbody>${list.map(i=>`<tr>
        <td class="num mute">${i.id}</td><td>${esc(i.name)}</td>
        <td class="mute">${esc(i.specialty||'—')}</td>
        <td class="mute num">${esc(i.phone||'—')}</td>
        <td class="num">${i.class_count}</td>
        <td><div class="row" style="justify-content:flex-end">
          <button class="sm" onclick='editInstructor(${JSON.stringify(i)})'>Edit</button>
          <button class="sm danger" onclick="archiveInstructor(${i.id})">Archive</button>
        </div></td></tr>`).join('')}</tbody></table></div>`;
  }

  if(which === 'archive'){
    const a = await api('/admin/archived');
    const sec = (title, items, render) => `<h2 style="font-size:13px">${title}</h2>
      <div class="box pad0">${items.length?`<table><tbody>${items.map(render).join('')}</tbody></table>`
        :'<div class="empty">Nothing archived.</div>'}</div>`;
    body.innerHTML =
      sec('Archived clients', a.clients, c=>`<tr><td>${esc(c.name_en)}</td>
        <td class="mute num">${esc(c.phone||'')}</td>
        <td style="text-align:right"><div class="row" style="justify-content:flex-end">
          <button class="sm" onclick="restoreClient(${c.id})">Restore</button>
          <button class="sm danger" onclick="purgeClient(${c.id},'${esc(c.name_en)}')">Delete permanently</button>
        </div></td></tr>`) +
      sec('Archived classes', a.classes, c=>`<tr>
        <td><span class="dot" style="background:${esc(c.colour)}"></span>${esc(c.name)}</td>
        <td style="text-align:right"><button class="sm danger"
          onclick="purgeClass(${c.id},'${esc(c.name)}')">Delete permanently</button></td></tr>`) +
      sec('Archived instructors', a.instructors, i=>`<tr><td>${esc(i.name)}</td>
        <td class="mute">${esc(i.specialty||'')}</td></tr>`);
  }
};

/* ---------- destructive actions ---------- */
function confirmBox(title, message, label, fn, danger=true){
  window._confirmFn = fn;
  openModal(`<h3>${esc(title)}</h3><div class="mh" style="margin:8px 0 0;line-height:1.7">${message}</div>
    <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="${danger?'danger':'pri'}" onclick="runConfirm()">${esc(label)}</button></div>`);
}
window.runConfirm = async () => {
  const fn = window._confirmFn; closeModal();
  try{ await fn(); }catch(e){ toast(e.message,'bad'); }
};

window.archiveClient = (id, name) => confirmBox('Archive client',
  `<b>${esc(name)}</b> will be hidden from lists and their card revoked immediately.
   Attendance history is kept, and you can restore them from the Archive tab.`,
  'Archive', async () => { await api('/admin/clients/'+id, {method:'DELETE'});
    toast('Client archived'); dmTab('clients'); });

window.restoreClient = async (id) => {
  await api(`/admin/clients/${id}/restore`, {method:'POST'});
  toast('Client restored'); dmTab('archive');
};

window.purgeClient = (id, name) => confirmBox('Delete permanently',
  `This erases <b>${esc(name)}</b> and every plan, card and scan belonging to them.
   It cannot be undone. If they have any recorded attendance, the system will refuse.`,
  'Delete permanently', async () => {
    await api(`/admin/clients/${id}?hard=true`, {method:'DELETE'});
    toast('Client deleted'); dmTab('archive'); });

window.archiveClass = (id, name) => confirmBox('Archive class',
  `<b>${esc(name)}</b> will be hidden and its future scheduled sessions removed.
   Past sessions and attendance are kept.`,
  'Archive', async () => { await api('/admin/classes/'+id, {method:'DELETE'});
    toast('Class archived'); dmTab('classes'); });

window.purgeClass = (id, name) => confirmBox('Delete permanently',
  `This erases <b>${esc(name)}</b>, all of its sessions and all enrolments.
   Refused if any attendance was recorded.`,
  'Delete permanently', async () => {
    await api(`/admin/classes/${id}?hard=true`, {method:'DELETE'});
    toast('Class deleted'); dmTab('archive'); });

window.archiveInstructor = (id) => confirmBox('Archive instructor',
  'They will be hidden from the list. Reassign their classes first, or this will be refused.',
  'Archive', async () => { await api('/admin/instructors/'+id, {method:'DELETE'});
    toast('Instructor archived'); dmTab('instructors'); });

window.deleteSession = (id) => deleteSessionFrom(id);

window.adjustBalance = async (cid) => {
  const c = await api('/clients/'+cid);
  const sub = c.subscriptions.find(s=>s.active);
  if(!sub) return toast('This client has no active plan','bad');
  openModal(`<h3>Adjust balance</h3>
    <div class="mh">${esc(c.name_en)} — corrections are logged with your name and reason.</div>
    <label>PLAN NAME</label><input id="a_plan" value="${esc(sub.plan)}">
    <div class="fieldrow">
      <div><label>SESSIONS TOTAL</label><input id="a_tot" type="number" value="${sub.sessions_total}"></div>
      <div><label>SESSIONS USED</label><input id="a_used" type="number" value="${sub.sessions_used}"></div>
    </div>
    <label>EXPIRES ON</label><input id="a_exp" type="date" value="${esc(sub.expires_on)}">
    <label>REASON</label><input id="a_why" placeholder="Refund for cancelled class, miscount…">
    <div class="acts"><button onclick="closeModal()">Cancel</button>
      <button class="pri" onclick="saveAdjust(${sub.id})">Save adjustment</button></div>`);
};
window.saveAdjust = async (sid) => {
  if(!val('a_why')) return toast('A reason is required','bad');
  await api('/admin/subscriptions/'+sid, {method:'PATCH', body:{
    plan:val('a_plan'), sessions_total:Number(val('a_tot')),
    sessions_used:Number(val('a_used')), expires_on:val('a_exp'), reason:val('a_why')}});
  closeModal(); toast('Balance adjusted'); render();
};

/* ============================================================ USERS */
route('/users', async () => {
  const list = await api('/admin/users');
  view.innerHTML = `
    <div class="head"><div><h1>Users</h1>
      <div class="sub">Who can sign in to this system</div></div>
      <button class="pri" onclick="newUser()">Add user</button></div>

    <div class="box pad0"><table>
      <thead><tr><th>USERNAME</th><th>NAME</th><th>ROLE</th><th>LAST SIGN-IN</th><th>STATUS</th><th></th></tr></thead>
      <tbody>${list.map(u=>`<tr>
        <td class="num">${esc(u.username)}${u.id===ME.id?' <span class="pill grey">you</span>':''}</td>
        <td class="mute">${esc(u.name||'—')}</td>
        <td><span class="pill ${u.role==='admin'?'info':'grey'}">${esc(u.role)}</span>
          ${u.must_change?'<span class="pill warn">must change password</span>':''}</td>
        <td class="mute num">${u.last_login?fmtFull(u.last_login):'never'}</td>
        <td>${u.id && list.find(x=>x.id===u.id) ? '' : ''}
          <span class="pill ${u.active===false?'bad':'ok'}">active</span></td>
        <td><div class="row" style="justify-content:flex-end">
          <button class="sm" onclick="resetUserPw(${u.id},'${esc(u.username)}')">Reset password</button>
          ${u.id!==ME.id?`<button class="sm" onclick="toggleRole(${u.id},'${u.role}')">Make ${u.role==='admin'?'staff':'admin'}</button>
          <button class="sm danger" onclick="disableUser(${u.id},'${esc(u.username)}')">Disable</button>`:''}
        </div></td></tr>`).join('')}</tbody></table></div>

    <div class="sub" style="margin-top:16px">
      Staff can use every daily screen and the reception kiosk. Admins additionally get
      the Data manager, Users and the activity log.</div>`;
});

window.newUser = () => openModal(`
  <h3>Add user</h3><div class="mh">They sign in at /login with these details.</div>
  <div class="fieldrow">
    <div><label>USERNAME</label><input id="u_un" autocomplete="off" placeholder="reception"></div>
    <div><label>FULL NAME</label><input id="u_nm" placeholder="Front desk"></div>
  </div>
  <label>PASSWORD</label><input id="u_pw" type="text" autocomplete="new-password">
  <div class="sub" style="margin-top:6px">At least 10 characters. Shown in plain text so you can pass it on.</div>
  <label>ROLE</label><select id="u_role">
    <option value="staff">Staff — daily screens and reception</option>
    <option value="admin">Admin — everything</option></select>
  <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="pri" onclick="saveUser()">Create user</button></div>`);

window.saveUser = async () => {
  try{
    await api('/admin/users', {method:'POST', body:{username:val('u_un'), password:val('u_pw'),
      name:val('u_nm'), role:$('#u_role').value}});
    closeModal(); toast('User created'); render();
  }catch(e){ toast(e.message,'bad'); }
};

window.toggleRole = async (id, role) => {
  try{
    await api('/admin/users/'+id, {method:'PATCH', body:{role: role==='admin'?'staff':'admin'}});
    toast('Role updated'); render();
  }catch(e){ toast(e.message,'bad'); }
};

window.disableUser = (id, name) => confirmBox('Disable user',
  `<b>${esc(name)}</b> will be signed out everywhere and cannot sign in again.`,
  'Disable', async () => {
    await api('/admin/users/'+id, {method:'PATCH', body:{active:false}});
    toast('User disabled'); render(); });

window.resetUserPw = (id, name) => openModal(`
  <h3>Reset password</h3><div class="mh">${esc(name)} will be asked to choose a new one at next sign-in.</div>
  <label>TEMPORARY PASSWORD</label><input id="r_pw" type="text" value="${randomPw()}">
  <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="pri" onclick="doReset(${id})">Reset</button></div>`);

window.doReset = async (id) => {
  try{
    await api(`/admin/users/${id}/reset`, {method:'POST', body:{new_password:val('r_pw')}});
    closeModal(); toast('Password reset'); render();
  }catch(e){ toast(e.message,'bad'); }
};

function randomPw(){
  const a = 'abcdefghjkmnpqrstuvwxyz', n = '23456789';
  const pick = s => s[Math.floor(Math.random()*s.length)];
  return [...Array(4)].map(()=>pick(a)).join('') + '-' +
         [...Array(4)].map(()=>pick(a)).join('') + '-' +
         [...Array(3)].map(()=>pick(n)).join('');
}

window.changeMyPassword = () => openModal(`
  <h3>Change your password</h3>
  <label>CURRENT PASSWORD</label><input id="c_cur" type="password">
  <label>NEW PASSWORD</label><input id="c_new" type="password">
  <div class="sub" style="margin-top:6px">At least 10 characters. You will be signed out on other devices.</div>
  <div class="acts"><button onclick="closeModal()">Cancel</button>
    <button class="pri" onclick="doChangePw()">Change</button></div>`);

window.doChangePw = async () => {
  try{
    await api('/auth/password', {method:'POST', body:{
      current:val('c_cur'), new_password:val('c_new')}});
    closeModal(); toast('Password changed — sign in again');
    setTimeout(()=>location.href='/login', 1200);
  }catch(e){ toast(e.message,'bad'); }
};

window.signOut = async () => {
  await fetch('/api/auth/logout', {method:'POST'});
  location.href = '/login';
};

/* ============================================================ AUDIT */
route('/audit', async () => {
  const rows = await api('/admin/audit?limit=200');
  const colour = a => ({delete:'bad', archive:'warn', adjust:'warn', revoke:'warn',
                        create:'ok', restore:'ok', login:'grey'}[a] || 'info');
  view.innerHTML = `
    <div class="head"><div><h1>Activity log</h1>
      <div class="sub">Every admin action, newest first</div></div></div>

    <div class="box pad0">
      ${rows.length?`<table>
        <thead><tr><th>WHEN</th><th>WHO</th><th>ACTION</th><th>RECORD</th><th>DETAIL</th></tr></thead>
        <tbody>${rows.map(r=>`<tr>
          <td class="mute num">${fmtFull(r.at)}</td>
          <td>${esc(r.username||'—')}</td>
          <td><span class="pill ${colour(r.action)}">${esc(r.action)}</span></td>
          <td class="mute">${esc(r.entity)}${r.entity_id?' #'+r.entity_id:''}</td>
          <td class="mute" style="font-family:var(--mono);font-size:11px;max-width:340px;
              overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.detail||'')}</td>
        </tr>`).join('')}</tbody></table>`
      :'<div class="empty">Nothing logged yet.</div>'}
    </div>`;
});

/* ============================================================ SETTINGS */
route('/settings', async () => {
  const s = await api('/settings');
  view.innerHTML = `
    <div class="head"><div><h1>Settings</h1>
      <div class="sub">Rules that change how sessions are charged</div></div></div>

    <div class="grid g2">
      <div class="box">
        <h3 style="font-size:17px">No-shows</h3>
        <div class="sub" style="margin:6px 0 16px">
          When a session is closed, anyone on the roster who never checked in is
          recorded as a no-show. This decides whether that costs them a session.</div>

        <label style="display:flex;align-items:center;gap:10px;margin:0;
                      font-family:var(--sans);font-size:14px;letter-spacing:0;
                      text-transform:none;color:var(--ink)">
          <input type="checkbox" id="s_charge" ${s.no_show_charges?'checked':''}>
          Charge a session for a no-show
        </label>
        <div class="hint">You can still untick individual people while closing a session,
          so this only sets the default.</div>

        <label>GRACE PERIOD FOR CANCELLATIONS (HOURS)</label>
        <input id="s_cancel" type="number" min="0" max="168" value="${s.cancel_hours}">
        <div class="hint">Guidance for staff on when to excuse someone. Not enforced
          automatically — reception decides at the point of closing.</div>

        <label>NAG ABOUT UNCLOSED SESSIONS AFTER (HOURS)</label>
        <input id="s_after" type="number" min="0" max="72" value="${s.close_after_hours}">
        <div class="hint">How long after a class ends before it appears on the dashboard
          as waiting to be closed.</div>

        <div class="divider"></div>

        <label style="display:flex;align-items:center;gap:10px;margin:0;
                      font-family:var(--sans);font-size:14px;letter-spacing:0;
                      text-transform:none;color:var(--ink)">
          <input type="checkbox" id="s_auto" ${s.auto_close?'checked':''}>
          Close finished sessions automatically
        </label>
        <div class="hint">Without this, a forgotten evening means no-shows are never charged.</div>

        <label>WAIT THIS LONG BEFORE CLOSING (HOURS)</label>
        <input id="s_autoh" type="number" min="1" max="168" value="${s.auto_close_hours}">
        <div class="hint">Long enough that staff can fix a bad evening before anyone is charged.
          24 hours is a sensible default.</div>

        <div class="row" style="margin-top:22px">
          <button class="pri" onclick="saveSettings()">Save settings</button>
        </div>
      </div>

      <div class="box">
        <h3 style="font-size:17px">How charging works</h3>
        <div class="stack" style="margin-top:14px;color:var(--mute);font-size:13.5px">
          <div class="listrow"><span><span class="pill ok">present</span> checked in, or marked present</span>
            <b style="color:var(--ink)">−1</b></div>
          <div class="listrow"><span><span class="pill bad">no-show</span> expected, never arrived</span>
            <b style="color:var(--ink)">−1</b></div>
          <div class="listrow"><span><span class="pill grey">excused</span> cancelled in time, or illness</span>
            <b style="color:var(--ink)">0</b></div>
        </div>
        <div class="divider"></div>
        <div class="sub" style="line-height:1.7">
          <b style="color:var(--ink)">The safety brake.</b> If a session recorded
          <b style="color:var(--ink)">zero</b> check-ins, automatic closing skips it and
          flags it on the dashboard instead. A class where nobody scanned is far more
          likely to be a broken scanner or a distracted receptionist than everyone
          skipping — and charging a room full of people who were present is the one
          mistake that is hard to walk back.
        </div>
        <div class="divider"></div>
        <div class="sub" style="line-height:1.7">
          Cancelling a class refunds everyone already charged for it. Cancelling one
          client's booking marks them excused ahead of time, so closing that session
          later skips them. Every charge is reversible from the attendance list.
        </div>
      </div>
    </div>`;
});

window.saveSettings = async () => {
  try{
    await api('/settings', {method:'PUT', body:{
      no_show_charges: $('#s_charge').checked,
      cancel_hours: Number(val('s_cancel')),
      close_after_hours: Number(val('s_after')),
      auto_close: $('#s_auto').checked,
      auto_close_hours: Number(val('s_autoh'))}});
    toast('Settings saved');
  }catch(e){ toast(e.message,'bad'); }
};
