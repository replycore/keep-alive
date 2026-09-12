/**
 * FridayDev 免费服务器自动续期 Cloudflare Worker
 *
 * 针对 https://control.fridaydev.fr / https://fridaydev.fr 编写。
 * 该控制面板是标准 Pterodactyl，没有原 keep-alive（aclclouds 专用）
 * 所需的 /api/client/servers/{id}/upgrade/renew 自定义路由，续期实际由
 * fridaydev 自有计费门户完成：
 *
 *   1. POST /php/login.php               邮箱+密码登录 -> 会话 cookie
 *   2. GET  /php/get_services.php        列出服务（含免费计划续期窗口）
 *   3. POST /php/renew_free_service.php  {uuid} 免费服续期 5 天
 *   4. POST /php/renew_server.php        {uuid} 付费/被挂起服续期（扣余额）
 *
 * 特性：
 *   - 定时（默认每 6 小时）自动检查并续期，结果发 Telegram
 *   - 会话 cookie 可缓存在 CF KV（可选），否则每次运行重新登录
 *   - WebUI 密码登录控制台 / API 手动触发
 *
 * 变量：
 *   - FD_EMAIL        (Secret, 必填) fridaydev 门户登录邮箱
 *   - FD_PASSWORD     (Secret, 必填) fridaydev 门户登录密码
 *   - FD_BASE         (vars, 可选)   门户基础地址，默认 https://fridaydev.fr
 *   - SERVICE_IDS     (vars, 可选)   服务 uuid 白名单，逗号分隔；不填自动全部
 *   - AUTO_RENEW_PAID (vars, 可选)   "1" 表示被挂起的付费服也尝试续期（需余额）
 *   - WEBUI_PASSWORD  (Secret, 可选) WebUI 登录密码
 *   - ADMIN_TOKEN     (Secret, 可选) API 入口令牌（header: X-Admin-Token）
 *   - TG_BOT_TOKEN / TG_CHAT_ID (Secret, 可选) Telegram 通知
 *
 * 可选 KV 绑定：FD_KV（存会话 cookie）。未绑定则每次运行都重新登录。
 */

const FD_BASE_DEFAULT = 'https://fridaydev.fr';
const TG_API = 'https://api.telegram.org';
const WEBUI_COOKIE = 'fdka_auth';
const WEBUI_SESSION_MS = 12 * 3600 * 1000; // WebUI 会话有效期 12h
// 门户会话缓存 TTL。真正是否仍有效由服务端决定：一旦接口返回 401（non connecté），
// 代码会自动作废缓存并用 FD_PASSWORD 重新登录重试，因此 TTL 只是安全上限，不是续期的唯一依据。
const SESSION_TTL_MS = 15 * 864e5; // 15 天

// 最近一次运行结果（内存缓存，Worker 实例存活期间有效）
let memoryState = { results: [], summary: '（尚无运行记录）', runAt: null };
// 进程内 cookie 缓存（warm start 时可复用，主要靠 KV）
let memorySession = null;

function setState(results, summary) {
  memoryState = { results, summary, runAt: Date.now() };
}

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function todayIso() {
  return new Date().toISOString();
}

/** 北京时间 UTC+8 */
function beijingTime(ts = Date.now()) {
  const d = new Date(ts + 8 * 3600 * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
  );
}

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtFreeWindow(daysLeft) {
  const n = Number(daysLeft);
  if (!Number.isFinite(n)) return '未知';
  if (n < 0) return '已过期';
  if (n < 1) return `${(n * 24).toFixed(1)} 小时`;
  return `${Math.floor(n)} 天`;
}

// ---------------------------------------------------------------------------
// Cookie 工具（Worker 手动跨重定向累积 Set-Cookie）
// ---------------------------------------------------------------------------

/** 从 Response 提取 Set-Cookie 头，返回 [{name,value,attrs}] */
function extractSetCookies(resp) {
  const jar = [];
  let headers;
  try {
    headers = resp.headers.getSetCookie ? resp.headers.getSetCookie() : [];
  } catch (_) {
    headers = resp.headers.get('set-cookie') ? [resp.headers.get('set-cookie')] : [];
  }
  for (const draft of headers) {
    const [pair, ...rest] = String(draft).split(';');
    const idx = pair.indexOf('=');
    if (idx <= 0) continue;
    const name = pair.slice(0, idx).trim();
    const value = pair.slice(idx + 1).trim();
    const attrs = rest.map((a) => a.trim().toLowerCase());
    if (!name) continue;
    // 同名去重（保留下次写入的）
    const existing = jar.findIndex((c) => c.name === name);
    const cookie = { name, value, attrs };
    if (existing >= 0) jar[existing] = cookie;
    else jar.push(cookie);
  }
  return jar;
}

/** cookie jar => Cookie 请求头 */
function cookieHeader(jar) {
  if (!jar || !jar.length) return '';
  return jar.map((c) => `${c.name}=${c.value}`).join('; ');
}

/** 手动跟随重定向并累计 cookie */
async function fetchWithCookies(url, init, jar) {
  let currentUrl = url;
  let opts = { ...init };
  let redirects = 0;
  while (true) {
    const headers = new Headers(opts.headers || {});
    const ch = cookieHeader(jar);
    if (ch) headers.set('Cookie', ch);
    opts.headers = headers;
    opts.redirect = 'manual';
    const resp = await fetch(currentUrl, opts);
    const set = extractSetCookies(resp);
    for (const c of set) {
      const existing = jar.findIndex((x) => x.name === c.name);
      if (existing >= 0) jar[existing] = c;
      else jar.push(c);
    }
    if (resp.status >= 300 && resp.status < 400) {
      const loc = resp.headers.get('location');
      if (!loc) return resp;
      if (redirects++ > 5) return resp;
      currentUrl = new URL(loc, currentUrl).toString();
      opts = { ...opts, method: 'GET', body: undefined, headers: new Headers() };
      continue;
    }
    return resp;
  }
}

// ---------------------------------------------------------------------------
// 门户 API
// ---------------------------------------------------------------------------
function fdBase(env) {
  return String(env.FD_BASE || FD_BASE_DEFAULT).replace(/\/$/, '');
}

/** 会话 cookie 持久化到 KV（KV 未绑定则跳过） */
async function persistSession(env, jar) {
  const kv = env.FD_KV || env.FDKV;
  if (!kv || !jar || !jar.length) return;
  const body = {
    jar,
    expiresAt: Date.now() + SESSION_TTL_MS,
    savedAt: Date.now(),
  };
  await kv.put('fd_session', JSON.stringify(body)).catch((e) => console.log('[session] KV 写入失败:', e.message));
}

/** 作废缓存会话（KV + 内存），强制下次密码登录 */
async function invalidateSession(env) {
  memorySession = null;
  const kv = env.FD_KV || env.FDKV;
  if (kv) {
    await kv.delete('fd_session').catch((e) => console.log('[session] KV 删除失败:', e.message));
  }
}

/**
 * 登录门户，返回 { jar, cached }。
 * opts.fresh=true 时忽略一切缓存直接用 FD_PASSWORD 登录（用于会话已失效的场景）。
 * 优先：KV 缓存会话 -> 内存会话 -> 密码登录。
 */
async function portalLogin(env, ctx, opts = {}) {
  const base = fdBase(env);
  const kv = env.FD_KV || env.FDKV;
  const fresh = Boolean(opts && opts.fresh);

  // 1) KV 缓存的会话（fresh 时跳过）
  if (!fresh && kv) {
    try {
      const raw = await kv.get('fd_session', 'json');
      if (raw && raw.jar && Array.isArray(raw.jar) && Date.now() < (raw.expiresAt || 0)) {
        console.log('[session] 使用 KV 缓存会话');
        return { jar: raw.jar, cached: true };
      }
    } catch (e) {
      console.log('[session] KV 读取失败:', e.message);
    }
  }

  // 2) Warm start 内存会话（fresh 时跳过）
  if (!fresh && memorySession && memorySession.jar && Date.now() < memorySession.expiresAt) {
    console.log('[session] 使用内存会话');
    return { jar: memorySession.jar, cached: true };
  }

  // 3) 密码登录
  console.log(fresh ? '[session] 会话已失效，使用密码重新登录' : '[session] 未命中缓存，使用密码登录');
  if (!env.FD_EMAIL || !env.FD_PASSWORD) {
    throw new Error('FD_EMAIL / FD_PASSWORD 未配置');
  }
  const jar = [];
  const context = {
    screen_width: 1920,
    screen_height: 1080,
    platform: 'linux',
    language: 'en-US',
    timezone: 'UTC',
  };
  // 门户会在 cookie 中校验设备信息
  for (const [k, v] of Object.entries({
    fd_screen: `${context.screen_width}x${context.screen_height}`,
    fd_platform: context.platform,
    fd_language: context.language,
    fd_tz: context.timezone,
  })) {
    jar.push({ name: k, value: v, attrs: [] });
  }

  const resp = await fetchWithCookies(
    `${base}/php/login.php`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'User-Agent': 'fridaydev-keepalive/1.0', Referer: `${base}/login/` },
      body: JSON.stringify({ email: env.FD_EMAIL, password: env.FD_PASSWORD, remember: true, client_context: context }),
    },
    jar
  );
  const text = await resp.text().catch(() => '');
  let data = null;
  try {
    data = JSON.parse(text);
  } catch (_) {
    data = null;
  }

  if (data && data.success === true) {
    memorySession = { jar, expiresAt: Date.now() + SESSION_TTL_MS };
    await persistSession(env, jar);
    console.log('[session] 登录成功');
    return { jar, cached: false };
  }

  if (data || resp.status !== 200) {
    const msg = data && data.error ? data.error : `HTTP ${resp.status}`;
    throw new Error(`门户登录失败: ${msg}`);
  }
  throw new Error('门户登录失败: 未知响应');
}

/** 调用门户带会话接口 */
async function portalGetJSON(env, jar, path) {
  const base = fdBase(env);
  const resp = await fetchWithCookies(`${base}${path}`, { method: 'GET', headers: { 'User-Agent': 'fridaydev-keepalive/1.0' } }, jar);
  const text = await resp.text().catch(() => '');
  let data = null;
  try {
    data = JSON.parse(text);
  } catch (_) {
    data = null;
  }
  return { status: resp.status, data };
}

/** 调用门户 POST JSON 接口 */
async function portalPostJSON(env, jar, path, body) {
  const base = fdBase(env);
  const resp = await fetchWithCookies(
    `${base}${path}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json', 'User-Agent': 'fridaydev-keepalive/1.0' }, body: JSON.stringify(body) },
    jar
  );
  const text = await resp.text().catch(() => '');
  let data = null;
  try {
    data = JSON.parse(text);
  } catch (_) {
    data = null;
  }
  return { status: resp.status, data };
}

// ---------------------------------------------------------------------------
// 续期逻辑
// ---------------------------------------------------------------------------

async function portalGetServices(env, jar) {
  const { status, data } = await portalGetJSON(env, jar, '/php/get_services.php');
  if (status === 401 || (data && data.success === false && /non connect/i.test(String(data.error || '')))) {
    return { ok: false, authError: true, services: [], detail: '会话失效（401）' };
  }
  if (!data || data.success !== true) {
    return { ok: false, authError: false, services: [], detail: `get_services HTTP ${status}: ${JSON.stringify(data).slice(0, 200)}` };
  }
  return { ok: true, authError: false, services: Array.isArray(data.services) ? data.services : [], detail: null };
}

function normalizeService(svc) {
  const s = svc || {};
  const freePlan = s.free_plan && typeof s.free_plan === 'object' ? s.free_plan : null;
  const suspended = Boolean(s.suspendbeforesuprdate && String(s.suspendbeforesuprdate).trim() !== '');
  return {
    uuid: String(s.uuid || ''),
    name: s.nom || '未命名',
    offre: s.offre || '',
    prix: s.prix ?? null,
    renouvellement: s.renouvellement || null,
    suspended,
    freePlan: freePlan
      ? {
          canRenew: Boolean(freePlan.can_renew),
          daysLeft: freePlan.days_left === undefined || freePlan.days_left === null ? null : Number(freePlan.days_left),
        }
      : null,
  };
}

function serviceDisplay(s) {
  return s.name && s.uuid ? `${s.name} (${s.uuid.slice(0, 8)})` : s.uuid || '?';
}

function isSessionError(status, data) {
  return status === 401 || (data && data.success === false && /non connect/i.test(String(data.error || '')));
}

async function renewService(env, jar, svc) {
  const name = serviceDisplay(svc);
  // 免费服务：走 renew_free_service.php
  if (svc.freePlan) {
    if (!svc.freePlan.canRenew) {
      const days = svc.freePlan.daysLeft;
      return {
        result: 'wait',
        detail: `未到续期窗口（剩余 ${fmtFreeWindow(days)}，can_renew=false，需剩 ≤2 天才可续期）`,
        name,
      };
    }
    const { status, data } = await portalPostJSON(env, jar, '/php/renew_free_service.php', { uuid: svc.uuid });
    if (isSessionError(status, data)) {
      return { result: 'session_expired', detail: '会话失效（401）', name };
    }
    if (data && data.success === true) {
      return { result: 'success', detail: `续期成功（免费 +5 天）${data.message ? ': ' + data.message : ''}`, name };
    }
    if (data && data.error) {
      const msg = String(data.error);
      // 门户返回的"未到窗口"类提示（如 HTTP 409 + days_left）
      if (/renouvellement possible|d[ée]j[àa]|renew|disponible|t[ôo]t|attend|pas/i.test(msg)) {
        return { result: 'wait', detail: `portal: ${msg}`, name };
      }
      return { result: 'error', detail: `portal: ${msg}`, name };
    }
    return { result: 'error', detail: `renew_free HTTP ${status}: ${JSON.stringify(data).slice(0, 200)}`, name };
  }

  // 付费 / 被挂起服务：走 renew_server.php（会扣余额）
  if (String(env.AUTO_RENEW_PAID) === '1') {
    const { status, data } = await portalPostJSON(env, jar, '/php/renew_server.php', { uuid: svc.uuid });
    if (isSessionError(status, data)) {
      return { result: 'session_expired', detail: '会话失效（401）', name };
    }
    if (data && data.success === true) {
      return { result: 'success', detail: '续期成功（扣余额）', name };
    }
    if (data && data.redirect) {
      return { result: 'error', detail: `需要充值: ${data.redirect}`, name };
    }
    return { result: 'error', detail: `renew_server HTTP ${status}: ${JSON.stringify(data).slice(0, 200)}`, name };
  }

  return { result: 'skip', detail: '付费服务需余额续期，未开启 AUTO_RENEW_PAID', name };
}

// ---------------------------------------------------------------------------
// 批量执行
// ---------------------------------------------------------------------------
async function runAll(env, ctx = null) {
  if (!env.FD_EMAIL || !env.FD_PASSWORD) {
    const err = [{ serverId: 'global', result: 'error', detail: 'FD_EMAIL / FD_PASSWORD 未配置', name: 'global' }];
    await sendTelegram(env, err, ctx);
    return { results: err, summary: 'global=error', anyError: true };
  }

  let session;
  try {
    session = await portalLogin(env, ctx);
  } catch (e) {
    console.error('登录失败:', e.message);
    const err = [{ serverId: 'global', result: 'error', detail: `登录失败: ${e.message}`, name: 'global' }];
    await sendTelegram(env, err, ctx);
    return { results: err, summary: 'global=error', anyError: true };
  }

  let list = await portalGetServices(env, session.jar);
  // 会话失效时：作废缓存，用密码强制重登一次后重试
  if (list.authError) {
    console.log('[session] KV/内存会话失效，作废缓存并用密码重新登录…');
    await invalidateSession(env);
    try {
      session = await portalLogin(env, ctx, { fresh: true });
      list = await portalGetServices(env, session.jar);
      if (list.authError) {
        await invalidateSession(env);
        list = { ok: false, services: [], detail: '重登后仍提示未登录，密码可能已变更' };
      }
    } catch (e) {
      list = { ok: false, services: [], detail: `重登失败: ${e.message}` };
    }
  }

  if (!list.ok) {
    const err = [{ serverId: 'global', result: 'error', detail: `服务列表获取失败: ${list.detail}`, name: 'global' }];
    await sendTelegram(env, err, ctx);
    return { results: err, summary: 'global=error', anyError: true };
  }

  // 服务列表成功获取 -> 刷新 KV 会话（服务端可能旋转了 PHPSESSID）
  await persistSession(env, session.jar);

  let services = (list.services || []).map(normalizeService).filter((s) => s.uuid);

  // 白名单过滤
  const manual = (env.SERVICE_IDS || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  if (manual.length) {
    services = services.filter((s) => manual.includes(s.uuid));
  }

  console.log(`[discover] 服务 ${list.services.length} 个，过滤后 ${services.length} 个`);

  if (!services.length) {
    const err = [{ serverId: 'global', result: 'error', detail: '未发现可处理的服务', name: 'global' }];
    await sendTelegram(env, err, ctx);
    return { results: err, summary: 'global=error', anyError: true };
  }

  const results = [];
  for (const svc of services) {
    try {
      let r = await renewService(env, session.jar, svc);
      // 续期请求暴露会话失效：作废缓存 + 密码重登一次 + 重试该服务
      if (r.result === 'session_expired') {
        console.log(`[${svc.uuid}] 续期时会话失效，作废缓存并用密码重新登录后重试…`);
        await invalidateSession(env);
        try {
          session = await portalLogin(env, ctx, { fresh: true });
          r = await renewService(env, session.jar, svc);
        } catch (e) {
          r = { result: 'error', detail: `会话失效且重登失败: ${e.message}`, name: r.name || serviceDisplay(svc) };
        }
        if (r.result === 'session_expired') {
          r = { result: 'error', detail: '会话失效且重试仍失败（凭证可能无效）', name: r.name || serviceDisplay(svc) };
        }
      }
      results.push({ ...r, serverId: svc.uuid });
    } catch (e) {
      console.error(`[${svc.uuid}] exception:`, e);
      results.push({ serverId: svc.uuid, name: serviceDisplay(svc), result: 'error', detail: `exception: ${e.message || e}` });
    }
    await new Promise((r) => setTimeout(r, 300));
  }

  const summary = results.map((r) => `${(r.name || r.serverId).slice(0, 20)}=${r.result}`).join(', ');
  const anyError = results.some((r) => r.result === 'error');
  console.log(`[done] ${summary}`);

  setState(results, summary);
  await sendTelegram(env, results, ctx);
  return { results, summary, anyError };
}

// ---------------------------------------------------------------------------
// Telegram 通知
// ---------------------------------------------------------------------------
const RESULT_EMOJI = { success: '✅', wait: '⏳', captcha: '👮', error: '❌', skip: '⚪', session_expired: '🔑' };
const RESULT_LABEL = { success: '续期成功', wait: '未到续期时间', captcha: '需人机验证', error: '失败', skip: '已跳过', session_expired: '会话失效' };

function serverBlock(r) {
  const emoji = RESULT_EMOJI[r.result] || '❓';
  const label = RESULT_LABEL[r.result] || r.result;
  let lines = [`<b>${emoji} ${escapeHtml(r.name || r.serverId)}</b> <code>${escapeHtml(r.serverId || '')}</code>`];
  if (r.suspended) lines.push(`  ⚠️ <b>已挂起</b>`);
  if (r.freePlan && r.suspended === undefined) lines.push(`  计划: 免费（5 天循环）`);
  if (r.result !== 'success' && r.detail) {
    lines.push(`  详情: <code>${escapeHtml(String(r.detail).slice(0, 300))}</code>`);
  }
  lines.push(`  状态: ${label}`);
  return lines.join('\n');
}

async function sendTelegram(env, results, ctx) {
  const token = env.TG_BOT_TOKEN;
  const chatId = env.TG_CHAT_ID;
  if (!token || !chatId) {
    console.log('TG 未配置（缺 TG_BOT_TOKEN 或 TG_CHAT_ID），跳过通知');
    return { sent: false, reason: 'not_configured' };
  }

  const count = (k) => results.filter((r) => r.result === k).length;

  let header = `🔁 <b>FridayDev 续期报告</b>\n`;
  header += `🕒 触发时间(北京): <code>${beijingTime()}</code>\n`;
  header += `📊 服务: ${results.length} 个`;
  if (results.length) header += `   ✅${count('success')} ⏳${count('wait')} ⚪${count('skip')} ❌${count('error')}`;
  header += `\n——————————————\n`;

  let body = results.map((r) => serverBlock(r)).join('\n\n');
  if (!body) body = '（无服务记录）';

  const text = `${header}${body}`;
  const finalText = text.length > 4000 ? `${text.slice(0, 3950)}\n…(已截断)` : text;

  const sendP = (async () => {
    const resp = await fetch(`${TG_API}/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text: finalText,
        parse_mode: 'HTML',
        disable_web_page_preview: true,
      }),
    });
    const data = await resp.json().catch(() => null);
    if (!resp.ok || !data?.ok) {
      throw new Error(`TG sendMessage HTTP ${resp.status}: ${JSON.stringify(data).slice(0, 200)}`);
    }
    return { sent: true, message_id: data.result?.message_id };
  })();
  ctx?.waitUntil?.(sendP.catch((e) => console.error('TG 发送异常:', e.message)));
  console.log(`TG 通知已创建（${results.length} 个结果）`);
  return { sent: true, scheduled: true };
}

// ---------------------------------------------------------------------------
// WebUI / API 入口
// ---------------------------------------------------------------------------
function webuiEnabled(env) {
  return !!(env.WEBUI_PASSWORD && env.WEBUI_PASSWORD.length);
}

function isAuthed(request, url, env) {
  if (webuiEnabled(env)) {
    const cookies = request.headers.get('cookie') || '';
    const m = cookies.match(new RegExp(`${WEBUI_COOKIE}=([^;]+)`));
    if (m && m[1] === env.WEBUI_PASSWORD) return true;
  }
  const headerToken = request.headers.get('x-admin-token');
  const queryToken = url.searchParams.get('token');
  if (env.ADMIN_TOKEN && (headerToken === env.ADMIN_TOKEN || queryToken === env.ADMIN_TOKEN)) return true;
  if (webuiEnabled(env)) return false;
  return !env.ADMIN_TOKEN;
}

function renderLoginPage(env, error = '') {
  return `<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 - FridayDev 续期</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#1e293b;padding:32px;border-radius:12px;width:320px}
h1{font-size:18px;margin:0 0 16px}
input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;margin-bottom:12px}
button{width:100%;padding:10px;border-radius:8px;border:none;background:#3b82f6;color:#fff;font-size:15px;cursor:pointer}
.error{color:#f87171;font-size:13px;margin-bottom:10px}
</style></head>
<body><div class="box">
<h1>🔒 FridayDev 续期控制台</h1>
${error ? `<div class="error">${escapeHtml(error)}</div>` : ''}
<form method="POST" action="/login">
<input type="password" name="password" placeholder="输入密码" autofocus required>
<button type="submit">登录</button>
</form>
</div></body></html>`;
}

function renderIndexPage(env, state) {
  const results = state?.results || [];
  const runAt = state?.runAt || null;
  const lastSummary = state?.summary || '（尚未运行）';

  const rows = results.length
    ? results
        .map(
          (r) => `<tr>
          <td>${RESULT_EMOJI[r.result] || '❓'}</td>
          <td>${escapeHtml(r.name || r.serverId)} <code>${escapeHtml(r.serverId || '')}</code></td>
          <td>${escapeHtml((RESULT_LABEL[r.result] || r.result))}</td>
          <td>${r.detail ? `<code>${escapeHtml(String(r.detail).slice(0, 120))}</code>` : '-'}</td>
        </tr>`
        )
        .join('\n')
    : '<tr><td colspan="4">暂无记录，点击「续期」或等待定时任务</td></tr>';

  return `<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FridayDev 续期控制台</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:20px}
h1{font-size:20px} h2{font-size:16px;margin-top:24px}.card{background:#1e293b;padding:20px;border-radius:12px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #334155}
code{background:#0f172a;padding:2px 6px;border-radius:4px;font-size:13px}
button{padding:8px 14px;border-radius:8px;border:none;background:#3b82f6;color:#fff;cursor:pointer;font-size:14px}
button:hover{opacity:.9}.muted{color:#94a3b8;font-size:13px}a{color:#93c5fd}
</style>
</head><body>
<h1>🔁 FridayDev 续期控制台</h1>
<p class="muted">上次运行: ${runAt ? beijingTime(new Date(runAt).getTime()) : '从未'} &nbsp;•&nbsp; <a href="/logout">退出</a></p>
<div class="card"><h2>最近一次结果</h2>
<p class="muted">${escapeHtml(lastSummary)}</p>
<button id="runAll" onclick="runAll()">🔄 续期</button>
<div id="progress" class="muted" style="margin-top:8px"></div></div>
<div class="card"><h2>服务明细</h2><table>
<thead><tr><th></th><th>服务</th><th>状态</th><th>详情</th></tr></thead>
<tbody>${rows}</tbody>
</table></div>
<script>
async function post(url, body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:body||null});
  return r.json();
}
async function runAll(){
  const btn=document.getElementById('runAll');const p=document.getElementById('progress');
  btn.disabled=true;p.textContent='执行中，请稍候…';
  try{
    const d=await post('/run');
    p.textContent='完成：' + (d.summary||'') + '（可刷新页面查看明细）';
  }catch(e){p.textContent='执行失败：'+e.message}
  btn.disabled=false;
}
</script>
</body></html>`;
}

// ---------------------------------------------------------------------------
// scheduled / fetch
// ---------------------------------------------------------------------------
export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runAll(env, ctx).catch((e) => console.error('scheduled error:', e)));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const ui = webuiEnabled(env);

    if (path === '/health') {
      return new Response(JSON.stringify({ ok: true, ts: todayIso() }), {
        headers: { 'content-type': 'application/json' },
      });
    }

    if (ui && path === '/login') {
      if (request.method === 'GET') {
        return new Response(renderLoginPage(env), { status: 200, headers: { 'content-type': 'text/html;charset=utf-8' } });
      }
      if (request.method === 'POST') {
        const form = await request.formData().catch(() => null);
        const pass = form?.get('password') || '';
        if (pass === env.WEBUI_PASSWORD) {
          const expires = new Date(Date.now() + WEBUI_SESSION_MS).toUTCString();
          const secure = url.protocol === 'https:' ? '; Secure' : '';
          return new Response('', {
            status: 302,
            headers: {
              location: '/',
              'set-cookie': `${WEBUI_COOKIE}=${env.WEBUI_PASSWORD}; Path=/; HttpOnly; SameSite=Lax${secure}; Expires=${expires}`,
            },
          });
        }
        return new Response(renderLoginPage(env, '密码错误'), { status: 401, headers: { 'content-type': 'text/html;charset=utf-8' } });
      }
    }

    if (ui && path === '/logout') {
      return new Response('', {
        status: 302,
        headers: { location: '/', 'set-cookie': `${WEBUI_COOKIE}=; Path=/; HttpOnly; Max-Age=0` },
      });
    }

    if (path === '/status') {
      if (!isAuthed(request, url, env)) {
        return new Response('{"error":"unauthorized"}', { status: 401, headers: { 'content-type': 'application/json' } });
      }
      const st = memoryState;
      return new Response(
        JSON.stringify({ ok: true, ts: todayIso(), lastRunAt: st.runAt, summary: st.summary, results: st.results }, null, 2),
        { headers: { 'content-type': 'application/json' } }
      );
    }

    if (path === '/run') {
      if (request.method !== 'POST') {
        return new Response('{"error":"method not allowed"}', { status: 405, headers: { 'content-type': 'application/json' } });
      }
      if (!isAuthed(request, url, env)) {
        return new Response('{"error":"unauthorized"}', { status: 401, headers: { 'content-type': 'application/json' } });
      }
      const res = await runAll(env, ctx);
      return new Response(JSON.stringify({ summary: res.summary, ran: res.results, ts: todayIso() }, null, 2), {
        headers: { 'content-type': 'application/json' },
      });
    }

    if (path === '/') {
      if (ui) {
        if (!isAuthed(request, url, env)) {
          return new Response(renderLoginPage(env), { status: 200, headers: { 'content-type': 'text/html;charset=utf-8' } });
        }
        return new Response(renderIndexPage(env, memoryState), { status: 200, headers: { 'content-type': 'text/html;charset=utf-8' } });
      }
      return new Response(JSON.stringify({ ok: true, ts: todayIso(), lastRunAt: memoryState.runAt, summary: memoryState.summary }), {
        headers: { 'content-type': 'application/json' },
      });
    }

    return new Response('{"error":"not found"}', { status: 404, headers: { 'content-type': 'application/json' } });
  },
};