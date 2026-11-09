const app = document.getElementById('app');
const idr = n => 'Rp ' + Number(n).toLocaleString('id-ID');
const when = s => new Date(s).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'});
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Bookings (id -> token) live in this browser only; the token is the customer's credential.
const store = {
  get() { try { return JSON.parse(localStorage.getItem('bookings') || '{}'); } catch { return {}; } },
  set(id, token) { try { const b = this.get(); b[id] = token; localStorage.setItem('bookings', JSON.stringify(b)); } catch {} },
};
let tokens = store.get();

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => null);
  if (!r.ok) {
    let d = body && body.detail;
    if (Array.isArray(d)) d = d.map(e => (e.loc || []).slice(1).join('.') + ': ' + e.msg).join('; ');
    throw new Error(d || 'Request failed (' + r.status + ')');
  }
  return body;
}
const authed = id => ({headers: {'X-Booking-Token': tokens[id] || ''}});
const show = html => { app.innerHTML = html; };
let timer;

async function home() {
  show('<h2>Activities</h2><div id="l">Loading…</div>');
  const list = await api('/activities');
  document.getElementById('l').innerHTML = list.length ? list.map(a =>
    `<div class="card"><div class="row"><div><b>${esc(a.name)}</b><div class="muted">${esc(a.description)}</div></div>
     <a class="btn" href="#/activity/${a.id}">See times</a></div></div>`).join('') : '<p class="muted">No activities yet.</p>';
}

async function activity(id) {
  show('Loading…');
  const [a, slots] = await Promise.all([api('/activities/' + id), api(`/activities/${id}/slots`)]);
  show(`<a class="back" href="#/">← All activities</a><h2>${esc(a.name)}</h2><p class="muted">${esc(a.description)}</p>` +
    (slots.length ? slots.map(s => `<div class="card"><div class="row"><div><b>${when(s.starts_at)}</b>
      <div class="muted">${idr(s.price_idr)} per person · ${s.seats_available} of ${s.capacity} seats left</div></div>
      ${s.seats_available ? `<a class="btn" href="#/slot/${s.id}/${s.seats_available}/${s.price_idr}">Book</a>` : '<span class="badge s-cancelled">Full</span>'}
      </div></div>`).join('') : '<p class="muted">No upcoming times.</p>'));
}

function bookForm(slotId, left, price) {
  const key = crypto.randomUUID(); // makes a double-click or retry safe on the server
  show(`<a class="back" onclick="history.back()">← Back</a><h2>Your details</h2>
  <form class="card" id="f"><label>Name<input name="customer_name" required maxlength="160"></label>
  <label>WhatsApp number<input name="customer_whatsapp" required pattern="\\+?[0-9]{8,15}" placeholder="+6281234567890"></label>
  <label>People (max ${Math.min(left, 50)})<input name="party_size" type="number" min="1" max="${Math.min(left, 50)}" value="1" required></label>
  <p class="muted" id="tot"></p><div class="err" id="e"></div><button>Hold my seats</button></form>`);
  const f = document.getElementById('f'), tot = () => document.getElementById('tot').textContent =
    'Total: ' + idr(price * (Number(f.party_size.value) || 0)) + ' · seats are held for a short time until you pay.';
  f.oninput = tot; tot();
  f.onsubmit = async ev => {
    ev.preventDefault();
    const btn = f.querySelector('button'); btn.disabled = true;
    try {
      const b = await api(`/slots/${slotId}/bookings`, {method: 'POST',
        headers: {'Content-Type': 'application/json', 'Idempotency-Key': key},
        body: JSON.stringify({customer_name: f.customer_name.value.trim(),
          customer_whatsapp: f.customer_whatsapp.value.replace(/\s/g, ''), party_size: Number(f.party_size.value)})});
      tokens[b.id] = b.access_token; store.set(b.id, b.access_token);
      location.hash = '#/booking/' + b.id;
    } catch (e) { document.getElementById('e').textContent = e.message; btn.disabled = false; }
  };
}

async function booking(id) {
  clearInterval(timer);
  if (!tokens[id]) return show('<p class="err">This booking is not saved in this browser.</p><a class="back" href="#/">Home</a>');
  const draw = async () => {
    let b;
    try { b = await api('/bookings/' + id, authed(id)); }
    catch (e) { clearInterval(timer); return show(`<p class="err">${esc(e.message)}</p><a class="back" href="#/">Home</a>`); }
    const pending = b.status === 'pending';
    const live = pending && new Date(b.hold_expires_at) > new Date();
    show(`<a class="back" href="#/mine">← My bookings</a><h2>Booking <span class="badge s-${b.status}">${b.status.replace('_', ' ')}</span></h2>
    <div class="card"><div><b>${esc(b.customer_name)}</b> · ${b.party_size} ${b.party_size > 1 ? 'people' : 'person'}</div>
    <div class="muted">${esc(b.customer_whatsapp)}</div><p>Total ${idr(b.total_idr)} · paid ${idr(b.paid_idr)}</p>
    ${b.refund_due_idr ? `<p class="err">Refund due: ${idr(b.refund_due_idr)}</p>` : ''}
    ${live ? `<p class="muted">Seats held until ${when(b.hold_expires_at)}</p>` : ''}
    <div class="err" id="e"></div>
    ${live ? '<button id="pay">Pay now</button> ' : ''}
    ${['pending', 'confirmed'].includes(b.status) ? '<button class="secondary danger" id="cx">Cancel booking</button>' : ''}</div>`);
    const err = m => document.getElementById('e').textContent = m;
    const pay = document.getElementById('pay'), cx = document.getElementById('cx');
    if (pay) pay.onclick = async () => { try { const c = await api(`/bookings/${id}/pay`, {method: 'POST', ...authed(id)}); location.href = c.payment_url; } catch (e) { err(e.message); } };
    if (cx) cx.onclick = async () => { if (!confirm('Cancel this booking?')) return;
      try { await api(`/bookings/${id}/cancel`, {method: 'POST', ...authed(id)}); draw(); } catch (e) { err(e.message); } };
    if (!pending) clearInterval(timer);
  };
  await draw();
  timer = setInterval(() => { if (location.hash.startsWith('#/booking/')) draw(); else clearInterval(timer); }, 5000);
}

async function mine() {
  const ids = Object.keys(tokens);
  if (!ids.length) return show('<h2>My bookings</h2><p class="muted">Nothing booked from this browser yet.</p>');
  show('<h2>My bookings</h2><div id="l">Loading…</div>');
  const rows = await Promise.all(ids.map(id => api('/bookings/' + id, authed(id)).catch(() => null)));
  document.getElementById('l').innerHTML = rows.filter(Boolean).map(b =>
    `<a class="card row" style="color:inherit;text-decoration:none" href="#/booking/${b.id}"><span><b>${esc(b.customer_name)}</b>
     <span class="muted"> · ${b.party_size} · ${idr(b.total_idr)}</span></span><span class="badge s-${b.status}">${b.status.replace('_', ' ')}</span></a>`).join('');
}

async function route() {
  clearInterval(timer);
  const [, name, a, b, c] = location.hash.split('/');
  try {
    if (name === 'activity') await activity(a);
    else if (name === 'slot') bookForm(a, Number(b), Number(c));
    else if (name === 'booking') await booking(a);
    else if (name === 'mine') await mine();
    else await home();
  } catch (e) { show(`<p class="err">${esc(e.message)}</p><a class="back" href="#/">Home</a>`); }
}
addEventListener('hashchange', route); route();
