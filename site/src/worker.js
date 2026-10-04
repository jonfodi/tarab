// tarab.top: serves the static page from ./public and takes waitlist signups at /api/waitlist.

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

async function joinWaitlist(request, env) {
  if (request.method !== 'POST') return json({ error: 'method not allowed' }, 405);

  // Only accept posts from the page itself.
  const origin = request.headers.get('origin');
  if (origin && new URL(origin).host !== new URL(request.url).host) return json({ error: 'forbidden' }, 403);

  let data;
  try {
    const type = request.headers.get('content-type') || '';
    data = type.includes('application/json') ? await request.json() : Object.fromEntries(await request.formData());
  } catch {
    return json({ error: 'bad request' }, 400);
  }

  // Hidden field people never see; bots fill in every field.
  if (data.website) return json({ ok: true });

  const email = String(data.email || '').trim().toLowerCase();
  if (email.length > 254 || !EMAIL.test(email)) return json({ error: 'invalid email' }, 400);

  // A repeat signup answers the same as a new one, so the form never reveals who is already on the list.
  await env.DB.prepare('INSERT INTO waitlist (email, created_at, country) VALUES (?, ?, ?) ON CONFLICT(email) DO NOTHING')
    .bind(email, new Date().toISOString(), request.cf?.country ?? null)
    .run();
  return json({ ok: true });
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === '/api/waitlist') return joinWaitlist(request, env);
    return env.ASSETS.fetch(request);
  },
};
