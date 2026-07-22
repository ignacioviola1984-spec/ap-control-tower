const COOKIE_NAME = 'ap_pitch_session';
const SESSION_SECONDS = 60 * 60 * 24;

const encoder = new TextEncoder();

function base64Url(bytes) {
  let binary = '';
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
}

async function digest(value) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(value)));
}

function safeEqual(left, right) {
  if (left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) mismatch |= left[index] ^ right[index];
  return mismatch === 0;
}

async function sign(value, secret) {
  const key = await crypto.subtle.importKey(
    'raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  return base64Url(new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(value))));
}

function cookieValue(request) {
  const header = request.headers.get('cookie') || '';
  const cookie = header.split(';').map((part) => part.trim()).find((part) => part.startsWith(`${COOKIE_NAME}=`));
  return cookie ? cookie.slice(COOKIE_NAME.length + 1) : null;
}

async function validSession(request, password) {
  const token = cookieValue(request);
  if (!token) return false;
  const [expiresRaw, signature] = token.split('.');
  const expires = Number.parseInt(expiresRaw, 10);
  if (!expires || Date.now() > expires || !signature) return false;
  return safeEqual(encoder.encode(signature), encoder.encode(await sign(expiresRaw, password)));
}

function loginPage(error = '', unavailable = false) {
  const title = unavailable ? 'Acceso no configurado' : 'Business Case privado';
  const message = unavailable
    ? 'El equipo debe configurar AP_PITCH_PASSWORD antes de compartir este micrositio.'
    : 'Ingresá la contraseña compartida por el equipo para acceder a AP Control Tower.';
  const errorMarkup = error ? `<p class="error" role="alert">${error}</p>` : '';
  return `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>AP Control Tower · Acceso</title><style>
  *{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;color:#f4f7fb;background:radial-gradient(circle at 75% 10%,rgba(19,181,232,.16),transparent 28rem),#07111f;font-family:Inter,system-ui,sans-serif}.card{width:min(100%,430px);padding:36px;border:1px solid rgba(151,184,214,.2);border-radius:24px;background:#0c1a2b;box-shadow:0 30px 80px rgba(0,0,0,.3)}.mark{display:grid;width:48px;height:48px;place-items:center;margin-bottom:30px;border:1px solid rgba(89,215,255,.4);border-radius:13px;color:#59d7ff;background:rgba(89,215,255,.07);font-weight:900}small{color:#59d7ff;font-weight:800;letter-spacing:.13em;text-transform:uppercase}h1{margin:10px 0 12px;font-size:34px;letter-spacing:-.04em}p{margin:0;color:#9fb0c4;line-height:1.55}.error{margin-top:16px;color:#ff9da2}label{display:block;margin-top:26px;color:#c8d3df;font-size:12px;font-weight:700}input{width:100%;height:50px;margin-top:8px;padding:0 14px;border:1px solid rgba(151,184,214,.22);border-radius:10px;outline:none;color:#fff;background:#07111f}input:focus{border-color:#59d7ff;box-shadow:0 0 0 3px rgba(89,215,255,.08)}button{width:100%;height:50px;margin-top:14px;border:0;border-radius:10px;color:#021019;background:linear-gradient(135deg,#59d7ff,#77efca);font-weight:900;cursor:pointer}footer{margin-top:26px;color:#61778e;font-size:10px}
  </style></head><body><main class="card"><div class="mark">AP</div><small>Private Business Case</small><h1>${title}</h1><p>${message}</p>${errorMarkup}${unavailable ? '' : '<form method="post"><label for="password">Contraseña<input id="password" name="password" type="password" autocomplete="current-password" required autofocus></label><button type="submit">Ingresar</button></form>'}<footer>Acceso restringido · No indexado · Sesión de 24 horas</footer></main></body></html>`;
}

function htmlResponse(markup, status = 200, headers = {}) {
  return new Response(markup, {
    status,
    headers: {
      'content-type': 'text/html; charset=utf-8',
      'cache-control': 'private, no-store',
      'x-robots-tag': 'noindex, nofollow, noarchive, nosnippet',
      ...headers,
    },
  });
}

export default async (request, context) => {
  const password = Netlify.env.get('AP_PITCH_PASSWORD');
  if (!password) return htmlResponse(loginPage('', true), 503);

  const url = new URL(request.url);
  if (url.searchParams.get('logout') === '1') {
    return htmlResponse(loginPage(), 200, {
      'set-cookie': `${COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`,
    });
  }

  if (await validSession(request, password)) return context.next();

  if (request.method === 'POST') {
    const form = await request.formData();
    const supplied = String(form.get('password') || '');
    if (safeEqual(await digest(supplied), await digest(password))) {
      const expires = Date.now() + SESSION_SECONDS * 1000;
      const token = `${expires}.${await sign(String(expires), password)}`;
      return new Response(null, {
        status: 303,
        headers: {
          location: '/',
          'set-cookie': `${COOKIE_NAME}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_SECONDS}`,
          'cache-control': 'private, no-store',
        },
      });
    }
    return htmlResponse(loginPage('La contraseña no es correcta.'), 401);
  }

  return htmlResponse(loginPage());
};
