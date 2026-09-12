/**
 * Pterodactyl 面板服务器自动续期 Cloudflare Worker（单服务商版）
 *
 * 功能：
 *   1. 基于 Pterodactyl 面板 Client API 自动续期
 *   2. 每 3 天 Cron 自动检查并续期，结果发 Telegram
 *   3. WebUI：密码登录（无用户名）+ 状态展示 + 手动触发
 *
 * 变量：
 *   - API_KEY        (Secret, 必填) 面板 Client API Key
 *   - API_BASE       (vars 可选)    API 基础地址，默认 aclclouds.com/api
 *   - SERVER_IDS     (vars 可选)    服务器白名单，逗号分隔；不填则自动发现全部
 *   - WEBUI_PASSWORD (Secret, 可选) WebUI 登录密码（不配置则无 WebUI）
 *   - ADMIN_TOKEN    (Secret, 可选) 手动触发 HTTP 入口的令牌（兼容）
 *   - TG_BOT_TOKEN   (Secret, 可选) Telegram Bot Token
 *   - TG_CHAT_ID     (Secret, 可选) Telegram 接收通知 chat id
 *
 * Cron: 每 3 天一次（wrangler.toml [triggers]）
 */

const TG_API = 'https://api.telegram.org';
const DEFAULT_API_BASE = 'https://aclclouds.com/api'; // 默认面板 API 基础地址
const WEBUI_COOKIE = 'keepalive_auth';
const WEBUI_SESSION_MS = 12 * 3600 * 1000; // WebUI 会话有效期 12h

// 最近一次运行结果（内存缓存，Worker 实例存活期间有效；重启后清空）
let memoryState = { results: [], summary: '（尚无运行记录）', runAt: null };

function setState(results, summary) {
  memoryState = { results, summary, runAt: Date.now() };
}

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function todayIso() {
  return new Date().toISOString();
}

/** 北京时间 (UTC+8) */
function beijingTime(ts = Date.now()) {
  const d = new Date(ts + 8 * 3600 * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
  );
}

/** 到期时间 -> 北京时间展示（带星期几） */
function formatExpiry(expiresAt) {
  if (!expiresAt) return '未知';
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return expiresAt;
  const d = new Date(t + 8 * 3600 * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  const week = ['日', '一', '二', '三', '四', '五', '六'][d.getUTCDay()];
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}(${week}) ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
  );
}

function formatRemaining(days) {
  if (days === null || days === undefined || Number.isNaN(days)) return '未知';
  if (days < 0) return `已过期 ${(-days * 24).toFixed(1)} 小时`;
  if (days < 1) return `${(days * 24).toFixed(1)} 小时`;
  const d = Math.floor(days);
  const h = Math.round((days - d) * 24);
  return `${d} 天 ${h} 小时`;
}

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// HTTP 封装
// ---------------------------------------------------------------------------
async function api(env, path, method = 'GET') {
  const base = String(env.API_BASE || DEFAULT_API_BASE).replace(/\/$/, '');
  const res = await fetch(base + path, {
    method,
    headers: {
      Authorization: `Bearer ${env.API_KEY}`,
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'User-Agent': 'server-keepalive/1.0',
    },
  });
  let body = null;
  try {
    body = await res.json();
  } catch (_) {
    body = await res.text().catch(() => null);
  }
  return { status: res.status, body, ok: res.ok };
}

// ---------------------------------------------------------------------------
// Pterodactyl 接口
// ---------------------------------------------------------------------------
async function listServers(env) {
  const { status, body } = await api(env, '/client');
  if (status !== 200) {
    return { ok: false, servers: [], detail: `GET /api/client HTTP ${status}: ${JSON.stringify(body).slice(0, 200)}` };
  }
  const data = (body && body.data) || [];
  const servers = data
    .map((item) => {
      const a = (item && item.attributes) || {};
      return {
        identifier: a.identifier,
        name: a.name || '',
        isFree: Boolean(a.is_free),
        expires_at: a.expires_at || null,
        canRenew: Boolean(a.can_renew),
      };
    })
    .filter((s) => s.identifier);
  return { ok: true, servers, detail: null };
}

async function getServer(env, identifier) {
  const { status, body } = await api(env, `/client/servers/${identifier}`);
  if (status !== 200) {
    return { ok: false, attrs: null, detail: `GET server HTTP ${status}: ${JSON.stringify(body).slice(0, 200)}` };
  }
  return { ok: true, attrs: (body && body.attributes) || {}, detail: null };
}

/**
 * 参考 Host-Ship Auto Renew：不绕过验证码 / Cloudflare / 安全验证，检测到就停下并指引人工处理。
 * 验证/人机相关关键词（扫描整段响应文本，不限于 403+单一字段）。
 */
const CAPTCHA_KEYWORDS = [
  'captcha', 'turnstile', 'human verification', 'verify you are human',
  'security check', 'security verification', 'cloudflare', '人机', '验证码',
  'verify human',
];

function looksLikeCaptcha(body) {
  const text = typeof body === 'string' ? body : JSON.stringify(body ?? '');
  if (!text) return null;
  const lower = text.toLowerCase();
  return CAPTCHA_KEYWORDS.find((k) => lower.includes(k)) || null;
}

/** 面板根地址（由 API_BASE 推导，如 https://aclclouds.com/api -> https://aclclouds.com） */
function panelBase(env) {
  const base = String(env.API_BASE || DEFAULT_API_BASE).replace(/\/$/, '');
  return base.replace(/\/?\/api[\/]?$/, '') || base;
}

async function renewServer(env, identifier) {
  const { status, body } = await api(env, `/client/servers/${identifier}/upgrade/renew`, 'POST');
  if (status === 200 || status === 201 || status === 202 || status === 204) {
    return { kind: 'success', detail: `续期成功 HTTP ${status}` };
  }
  if (status === 400 && body && body.error === 'renewal_not_available') {
    return { kind: 'wait', detail: `renewal_not_available days_remaining=${body.days_remaining}` };
  }
  // 任何非 2xx：扫描整段响应文本（含 errors[]/detail/message/error 全部字段），命中即判人机验证
  const keyword = looksLikeCaptcha(body);
  if (keyword) {
    const panelUrl = `${panelBase(env)}/server/${identifier}`;
    return {
      kind: 'captcha',
      detail:
        `HTTP ${status} 检测到人机验证(${keyword})：续期需人工处理，请到面板手动续期 ${panelUrl} ` +
        `响应: ${JSON.stringify(body).slice(0, 200)}`,
    };
  }
  if (status === 403) {
    const code = (body && (body.code || body.error)) || '';
    return { kind: 'error', detail: `403 ${code}` };
  }
  return { kind: 'error', detail: `renew HTTP ${status}: ${JSON.stringify(body).slice(0, 200)}` };
}

// ---------------------------------------------------------------------------
// 单服务器处理
// ---------------------------------------------------------------------------
function expiryRemainingDays(expiresAt) {
  if (!expiresAt) return null;
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return null;
  return (t - Date.now()) / 86400000;
}

async function processServer(env, identifier) {
  const srv = await getServer(env, identifier);
  if (!srv.ok) {
    console.log(`[${identifier}] [ERROR] ${srv.detail}`);
    return {
      serverId: identifier, result: 'error', detail: srv.detail,
      name: identifier, plan: null, expiresAt: null, newExpiresAt: null,
      remainingDays: null, isFree: null, isSuspended: null, renewalDays: null,
    };
  }
  const attrs = srv.attrs;
  const canRenew = Boolean(attrs.can_renew);
  const remaining = expiryRemainingDays(attrs.expires_at);
  const plan = attrs.plan || {};

  const base = {
    serverId: identifier,
    name: attrs.name || identifier,
    node: attrs.node || null,
    isFree: Boolean(attrs.is_free),
    isSuspended: Boolean(attrs.is_suspended),
    expiresAt: attrs.expires_at || null,
    newExpiresAt: null,
    remainingDays: remaining,
    renewalDays: plan.renewal_days ?? null,
    planName: plan.name || null,
    planCategory: plan.category || null,
    priceEur: plan.price_eur ?? null,
    connection: attrs.connection_addresses?.primary || null,
  };

  console.log(
    `[${identifier}] ${attrs.name || ''} expires=${attrs.expires_at} remaining=${formatRemaining(remaining)} ` +
      `can_renew=${canRenew} suspended=${base.isSuspended} renewal_days=${plan.renewal_days}`
  );

  if (!canRenew) {
    const detail = `未到续期窗口（can_renew=false，剩余 ${formatRemaining(remaining)}）`;
    console.log(`[${identifier}] ${detail}`);
    return { ...base, result: 'wait', detail };
  }

  const r = await renewServer(env, identifier);
  console.log(`[${identifier}] [${r.kind}] ${r.detail}`);

  if (r.kind === 'success') {
    const after = await getServer(env, identifier);
    if (after.ok) {
      base.newExpiresAt = after.attrs.expires_at || null;
      base.remainingDays = expiryRemainingDays(after.attrs.expires_at);
      console.log(`[${identifier}] [SUCCESS] 新 expires_at=${after.attrs.expires_at} 剩余 ${formatRemaining(base.remainingDays)}`);
    }
    return { ...base, result: 'success', detail: r.detail };
  }

  return { ...base, result: r.kind, detail: r.detail };
}

// ---------------------------------------------------------------------------
// Telegram 通知
// ---------------------------------------------------------------------------
const RESULT_EMOJI = { success: '✅', wait: '⏳', captcha: '👮', error: '❌', skip: '⚪' };
const RESULT_LABEL = { success: '续期成功', wait: '未到续期时间', captcha: '需人机验证', error: '失败', skip: '已跳过' };

function serverBlock(r) {
  const emoji = RESULT_EMOJI[r.result] || '❓';
  const lines = [`<b>${emoji} ${escapeHtml(r.name)}</b> <code>${escapeHtml(r.serverId)}</code>`];
  if (r.planName) {
    const free = r.isFree ? '免费' : '付费';
    lines.push(`  计划: ${escapeHtml(r.planName)} (${free}${r.priceEur ? ' €' + r.priceEur : ''}${r.renewalDays ? ` / ${r.renewalDays}天续期` : ''})`);
  }
  if (r.node) lines.push(`  节点: ${escapeHtml(r.node)}`);
  const showExpiry = r.newExpiresAt || r.expiresAt;
  if (showExpiry) lines.push(`  到期: ${formatExpiry(showExpiry)}`);
  if (r.remainingDays !== null && r.remainingDays !== undefined) {
    lines.push(`  剩余: ${formatRemaining(r.remainingDays)}`);
  }
  if (r.isSuspended) lines.push(`  ⚠️ <b>已暂停</b>`);
  if (r.result === 'captcha') {
    lines.push(`  状态: <b>${RESULT_LABEL[r.result]} — 需到网页手动续期</b>`);
  } else {
    lines.push(`  状态: ${RESULT_LABEL[r.result] || r.result}`);
  }
  if (r.detail && r.result !== 'success') {
    lines.push(`  详情: <code>${escapeHtml(String(r.detail).slice(0, 300))}</code>`);
  }
  return lines.join('\n');
}

async function sendTelegram(env, results, ctx) {
  const token = env.TG_BOT_TOKEN;
  const chatId = env.TG_CHAT_ID;
  if (!token || !chatId) {
    console.log('TG 未配置（缺 TG_BOT_TOKEN 或 TG_CHAT_ID），跳过通知');
    return { sent: false, reason: 'not_configured' };
  }

  const successCount = results.filter((r) => r.result === 'success').length;
  const waitCount = results.filter((r) => r.result === 'wait').length;
  const errorCount = results.filter((r) => r.result === 'error').length;
  const captchaCount = results.filter((r) => r.result === 'captcha').length;

  let header = `🔁 <b>服务器保活报告</b>\n`;
  header += `🕒 触发时间(北京): <code>${beijingTime()}</code>\n`;
  header += `📊 服务器: ${results.length} 台`;
  if (results.length > 0) header += `   ✅${successCount} ⏳${waitCount} 👮${captchaCount} ❌${errorCount}`;
  header += `\n——————————————\n`;

  let body = results.map((r) => serverBlock(r)).join('\n\n');
  if (!body) body = '（无服务器记录）';

  const text = `${header}${body}`;
  const finalText = text.length > 4000 ? `${text.slice(0, 3950)}\n…(已截断)` : text;

  const sendP = (async () => {
    const url = `${TG_API}/bot${token}/sendMessage`;
    const resp = await fetch(url, {
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
  console.log(`TG 通知已创建（${results.length} 台结果）`);
  return { sent: true, scheduled: true };
}

// ---------------------------------------------------------------------------
// 批量执行
// ---------------------------------------------------------------------------
async function runAll(env, ctx = null) {
  if (!env.API_KEY) {
    const err = [{ serverId: 'global', result: 'error', detail: 'API_KEY 未配置' }];
    console.error(err[0].detail);
    await sendTelegram(env, err, ctx);
    return { results: err, summary: 'global=error', anyError: true };
  }

  // 自动发现（或按 SERVER_IDS 白名单过滤）
  const manual = (env.SERVER_IDS || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  const list = await listServers(env);
  if (!list.ok) {
    console.error(`服务器列表获取失败: ${list.detail}`);
    const err = [{ serverId: 'global', result: 'error', detail: list.detail }];
    await sendTelegram(env, err, ctx);
    return { results: err, summary: 'global=error', anyError: true };
  }

  let ids = list.servers.map((s) => s.identifier);
  if (manual.length) {
    ids = ids.filter((id) => manual.includes(id));
    console.log(`[discover] 发现 ${list.servers.length} 台，按 SERVER_IDS 过滤后 ${ids.length} 台`);
  } else {
    console.log(`[discover] 自动发现 ${ids.length} 台服务器: ${ids.join(', ') || '(无)'}`);
  }

  if (!ids.length) {
    const err = [{ serverId: 'global', result: 'error', detail: '未发现服务器' }];
    await sendTelegram(env, err, ctx);
    return { results: err, summary: 'global=error', anyError: true };
  }

  const results = [];
  for (const id of ids) {
    try {
      results.push(await processServer(env, id));
    } catch (e) {
      console.error(`[${id}] exception:`, e);
      results.push({ serverId: id, result: 'error', detail: `exception: ${e.message || e}` });
    }
    // 轻微间隔，避免请求过密
    await new Promise((r) => setTimeout(r, 300));
  }

  const summary = results.map((r) => `${r.serverId}=${r.result}`).join(', ');
  const anyError = results.some((r) => r.result === 'error' || r.result === 'captcha');
  console.log(`[done] ${summary}`);

  setState(results, summary);
  await sendTelegram(env, results, ctx);
  return { results, summary, anyError };
}

// ---------------------------------------------------------------------------
// WebUI（密码登录，无用户名）
// ---------------------------------------------------------------------------

function webuiEnabled(env) {
  return !!(env.WEBUI_PASSWORD && env.WEBUI_PASSWORD.length);
}

/** 校验请求中的登录 cookie / ADMIN_TOKEN / query token */
function isAuthed(request, url, env) {
  // WebUI session cookie
  if (webuiEnabled(env)) {
    const cookies = request.headers.get('cookie') || '';
    const m = cookies.match(new RegExp(`${WEBUI_COOKIE}=([^;]+)`));
    if (m && m[1] === env.WEBUI_PASSWORD) return true;
  }
  // ADMIN_TOKEN 兼容：header / query
  const headerToken = request.headers.get('x-admin-token');
  const queryToken = url.searchParams.get('token');
  if (env.ADMIN_TOKEN && (headerToken === env.ADMIN_TOKEN || queryToken === env.ADMIN_TOKEN)) return true;
  // WebUI 已启用但没有 session cookie -> 需登录
  if (webuiEnabled(env)) return false;
  // 非 WebUI 模式：未配置任何鉴权 -> 放行
  return !env.ADMIN_TOKEN;
}

function renderLoginPage(env, error = '') {
  return `<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 - 服务器保活</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#1e293b;padding:32px;border-radius:12px;width:320px}
h1{font-size:18px;margin:0 0 16px}
input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;margin-bottom:12px}
button{width:100%;padding:10px;border-radius:8px;border:none;background:#3b82f6;color:#fff;font-size:15px;cursor:pointer}
.error{color:#f87171;font-size:13px;margin-bottom:10px}
</style></head>
<body><div class="box">
<h1>🔒 服务器保活控制台</h1>
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
  const apiBase = String(env.API_BASE || '默认');

  let resultsHtml = results.length
    ? results
        .map((r) => `<tr>
          <td>${RESULT_EMOJI[r.result] || '❓'}</td>
          <td>${escapeHtml(r.name || r.serverId)} <code>${escapeHtml(r.serverId)}</code></td>
          <td>${r.newExpiresAt || r.expiresAt ? formatExpiry(r.newExpiresAt || r.expiresAt) : '-'}</td>
          <td>${r.remainingDays !== null && r.remainingDays !== undefined ? formatRemaining(r.remainingDays) : '-'}</td>
          <td>${RESULT_LABEL[r.result] || r.result}</td>
        </tr>`)
        .join('\n')
    : '<tr><td colspan="5">暂无记录，点击「续期」或等待定时任务</td></tr>';

  return `<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>服务器保活控制台</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:20px}
h1{font-size:20px} h2{font-size:16px;margin-top:24px}.card{background:#1e293b;padding:20px;border-radius:12px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #334155}
code{background:#0f172a;padding:2px 6px;border-radius:4px;font-size:13px}
button{padding:8px 14px;border-radius:8px;border:none;background:#3b82f6;color:#fff;cursor:pointer;font-size:14px}
button:hover{opacity:.9}.muted{color:#94a3b8;font-size:13px}a{color:#93c5fd}
</style>
</head><body>
<h1>🖥️ 服务器保活控制台</h1>
<p class="muted">上次运行: ${runAt ? beijingTime(new Date(runAt).getTime()) : '从未'} &nbsp;•&nbsp; API: <code>${escapeHtml(apiBase)}</code> &nbsp;•&nbsp; <a href="/logout">退出</a></p>

<div class="card"><h2>最近一次结果</h2>
<p class="muted">${escapeHtml(lastSummary)}</p>
<button id="runAll" onclick="runAll()">🔄 续期</button>
<div id="progress" class="muted" style="margin-top:8px"></div></div>

<div class="card"><h2>服务器明细</h2><table>
<thead><tr><th></th><th>服务器</th><th>到期</th><th>剩余</th><th>状态</th></tr></thead>
<tbody>${resultsHtml}</tbody>
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
    p.textContent='完成：'+ (d.summary||'') + '（可刷新页面查看明细）';
  }catch(e){p.textContent='执行失败：'+e.message}
  btn.disabled=false;
}
</script>
</body></html>`;
}

// ---------------------------------------------------------------------------
// scheduled / fetch 入口
// ---------------------------------------------------------------------------
export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runAll(env, ctx).catch((e) => console.error('scheduled error:', e)));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const ui = webuiEnabled(env);

    // ---- 公开端点 ----
    if (path === '/health') {
      return new Response(JSON.stringify({ ok: true, ts: todayIso() }), {
        headers: { 'content-type': 'application/json' },
      });
    }

    // ---- WebUI 登录 ----
    if (ui && path === '/login') {
      if (request.method === 'GET') {
        return new Response(renderLoginPage(env), {
          status: 200,
          headers: { 'content-type': 'text/html;charset=utf-8' },
        });
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
        return new Response(renderLoginPage(env, '密码错误'), {
          status: 401,
          headers: { 'content-type': 'text/html;charset=utf-8' },
        });
      }
    }

    // ---- WebUI 退出 ----
    if (ui && path === '/logout') {
      return new Response('', {
        status: 302,
        headers: { location: '/', 'set-cookie': `${WEBUI_COOKIE}=; Path=/; HttpOnly; Max-Age=0` },
      });
    }

    // ---- WebUI 首页（需登录）----
    if (ui && path === '/') {
      if (!isAuthed(request, url, env)) {
        return new Response(renderLoginPage(env), {
          status: 200,
          headers: { 'content-type': 'text/html;charset=utf-8' },
        });
      }
      return new Response(renderIndexPage(env, memoryState), {
        status: 200,
        headers: { 'content-type': 'text/html;charset=utf-8' },
      });
    }

    // ---- WebUI 手动触发 ----
    if (ui && path === '/run') {
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

    // ---- 非 WebUI 模式：旧 API 模式 ----
    if (!ui && path === '/') {
      if (!isAuthed(request, url, env)) {
        return new Response('{"error":"unauthorized"}', { status: 403, headers: { 'content-type': 'application/json' } });
      }
      const res = await runAll(env, ctx);
      return new Response(JSON.stringify({ ran: res.results, summary: res.summary, ts: todayIso() }, null, 2), {
        headers: { 'content-type': 'application/json' },
      });
    }

    return new Response('{"error":"not found"}', { status: 404, headers: { 'content-type': 'application/json' } });
  },
};