#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aclclouds 免费服务器自动续期（GitHub Actions 版）
参考 Host-Ship Auto Renew 的工程骨架：每日 cron + 可选代理出口 + TG 通知 + 不绕过验证码。

区别（针对 aclclouds / Pterodactyl 面板）：
  - 续期走面板真实存在的 Client API（/upgrade/renew），不用浏览器猜 DOM，更稳更快。
  - 面板要求续期时若先返回 renewal_not_available → 未到窗口，跳过。
  - 返回 403 / 人机验证（captcha、turnstile、cloudflare、人机、验证码…）→ 停止、不反复尝试、
    在 TG 里给出【面板人工续期直链】，请用户人工处理（不绕过任何安全验证）。
  - 可选 PROXY_SERVER：续期请求经该 Socks5/HTTP 出口发出（可用于面板对出口 IP 有限制的情形）。

GitHub Secrets:
  API_KEY        (必填) aclclouds 面板 Client API Key（账号设置 -> API 凭据，ptlc_ 开头）
  API_BASE       (可选) 默认 https://aclclouds.com/api
  SERVER_IDS     (可选) 逗号分隔白名单，不填自动发现全部
  PROXY_SERVER   (可选) 出口代理，如 socks5://user:pass@host:port

  TG_BOT_TOKEN / TG_CHAT_ID  (可选) Telegram 通知

触发：GitHub Actions 每天早上 08:00（北京时间），也可手动 Run workflow。
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

L = ZoneInfo("Asia/Shanghai")  # 北京时间


def log(msg):
    print(f"[{datetime.now(L).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def beijing_now():
    return datetime.now(L)


def env_str(name, default=""):
    return os.getenv(name, default).strip()


API_KEY = env_str("API_KEY")
API_BASE = env_str("API_BASE", "https://aclclouds.com/api").rstrip("/")
SERVER_IDS = [s.strip() for s in env_str("SERVER_IDS").split(",") if s.strip()]
PROXY_SERVER = env_str("PROXY_SERVER")

TG_BOT_TOKEN = env_str("TG_BOT_TOKEN")
TG_CHAT_ID = env_str("TG_CHAT_ID")

MANUAL_RUN = env_str("MANUAL_RUN", "false").lower() == "true"

# 验证/人机相关关键词：扫整段响应文本（不限字段、不限状态码），参考 Host-Ship 的做法
CAPTCHA_KEYWORDS = [
    "captcha", "turnstile", "human verification", "verify you are human",
    "security check", "security verification", "verify human",
    "cloudflare", "人机", "验证码", "人机验证",
]


def proxy_dict():
    if not PROXY_SERVER:
        return None
    return {"http": PROXY_SERVER, "https": PROXY_SERVER}


def tg(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False
    try:
        r = requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TG_BOT_TOKEN,
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20, proxies=proxy_dict(),
        )
        if r.ok:
            log("✅ Telegram 通知发送成功")
            return True
        log("❌ Telegram 通知失败: %s" % r.text[:200])
        return False
    except Exception as exc:
        log("❌ Telegram 通知异常: %s" % exc)
        return False


def current_ip():
    try:
        r = requests.get("https://api.ipify.org", timeout=15, proxies=proxy_dict())
        return r.text.strip() if r.ok else "获取失败"
    except Exception:
        return "获取失败"


def panel_root():
    """API_BASE -> 面板根，如 https://aclclouds.com/api -> https://aclclouds.com"""
    return re.sub(r"/?(?:/api)?/?$", "", API_BASE) or API_BASE


def server_url(identifier):
    return "%s/server/%s" % (panel_root(), identifier)


def api(path, method="GET"):
    """aclclouds Pterodactyl Client API 封装（输出出口 IP 也能换）"""
    url = API_BASE + path
    try:
        r = requests.request(
            method, url,
            headers={
                "Authorization": "Bearer " + API_KEY,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "aclclouds-renew-actions/1.0",
            },
            timeout=30, proxies=proxy_dict(),
        )
        try:
            body = r.json()
        except Exception:
            body = r.text
        return r.status_code, body
    except Exception as exc:
        return 999, {"error": str(exc)}


def list_servers():
    status, body = api("/client")
    if status != 200:
        return None, "GET /api/client HTTP %s: %s" % (status, str(body)[:200])
    servers = []
    for item in (body or {}).get("data", []) or []:
        a = (item or {}).get("attributes", {})
        if not a.get("identifier"):
            continue
        servers.append({
            "identifier": a["identifier"],
            "name": a.get("name") or a["identifier"],
            "expires_at": a.get("expires_at"),
            "can_renew": bool(a.get("can_renew")),
            "is_free": bool(a.get("is_free")),
            "is_suspended": bool(a.get("is_suspended")),
        })
    return servers, None


def expired_days(expires_at):
    if not expires_at:
        return None
    try:
        t = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return (t - datetime.now().astimezone()).days
    except Exception:
        return None


def renew_server(identifier):
    """POST /upgrade/renew；任何非 2xx 都扫整段响应文本找验证关键词（不漏检）"""
    status, body = api(
        "/client/servers/%s/upgrade/renew" % identifier, method="POST"
    )
    if 200 <= status < 300:
        return {"kind": "success", "detail": "续期成功 HTTP %s" % status}

    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    hit = next((k for k in CAPTCHA_KEYWORDS if k in text.lower()), None)
    if hit:
        return {
            "kind": "captcha",
            "detail": ("HTTP %s 检测到人机验证(%s)：续期需人工处理，请到面板手动续期 %s "
                       "(不绕过验证码) 响应: %s"
                       % (status, hit, server_url(identifier), text[:200])),
        }
    return {"kind": "error", "detail": "renew HTTP %s: %s" % (status, text[:200])}


SERVER_NAME = "<unknown>"


def check_server(identifier):
    global SERVER_NAME
    status, body = api("/client/servers/%s" % identifier)
    if status != 200:
        return {"serverId": identifier, "result": "error",
                "detail": "GET server HTTP %s" % status}
    attrs = (body or {}).get("attributes", {})
    SERVER_NAME = attrs.get("name") or identifier
    remaining = expired_days(attrs.get("expires_at"))
    plan = (attrs.get("plan") or {}) or {}
    base = {
        "serverId": identifier,
        "name": SERVER_NAME,
        "can_renew": bool(attrs.get("can_renew")),
        "remaining_days": remaining,
        "expires_at": attrs.get("expires_at"),
        "is_suspended": bool(attrs.get("is_suspended")),
        "plan": plan.get("name"),
        "renewal_days": plan.get("renewal_days"),
    }

    if not base["can_renew"]:
        base["result"] = "wait"
        base["detail"] = ("未到续期窗口（can_renew=false，剩余约 %s 天）"
                          % ("未知" if remaining is None else remaining))
        return base

    r = renew_server(identifier)
    base["result"] = r["kind"]
    base["detail"] = r["detail"]
    return base


def server_block(r):
    d = r
    free = "免费" if d.get("is_free") else "付费"
    plan = (" / %s天续期" % d["renewal_days"]) if d.get("renewal_days") else ""
    name = str(d.get("name") or d["serverId"]).replace("&", "&amp;").replace("<", "&lt;")
    idx = d["serverId"].replace("&", "&amp;").replace("<", "&lt;")
    lines = [
        "<b>%s</b> <code>%s</code>" % ("🖥️", (name + " #" + idx)),
        "  计划: %s (%s%s)" % (d.get("plan") or "未知", free, plan),
    ]
    if d.get("expires_at"):
        lines.append("  到期: %s" % d["expires_at"].replace("T", " ").split("+")[0])
    if d.get("remaining_days") is not None:
        lines.append("  剩余: %s 天" % d["remaining_days"])
    if d.get("is_suspended"):
        lines.append("  ⚠️ 已暂停")
    emojis = {"success": "✅", "wait": "⏳", "captcha": "👮", "error": "❌"}
    lines.append("  状态: %s" % (emojis.get(d.get("result"), "❓") or "❓"))
    if d.get("result") == "captcha":
        lines.insert(-1, "  🔗 <a href=\"%s\">点击到面板手动续期</a>" % server_url(d["serverId"]))
    if d.get("detail") and d.get("result") not in ("wait",):
        lines.insert(0, "  详情: %s" % str(d["detail"]).replace("&", "&amp;").replace("<", "&lt;"))
    return "\n".join(lines)


def build_report(results, ip):
    lines = [
        "🔁 <b>aclclouds 续期检查</b>",
        "🕗 检查时间: %s" % beijing_now().strftime("%Y/%m/%d %H:%M"),
        "📍 出口IP: %s（代理: %s）" % (ip, "已启用" if PROXY_SERVER else "直连"),
    ]
    ok = sum(1 for r in results if r.get("result") == "success")
    wait = sum(1 for r in results if r.get("result") == "wait")
    cap = sum(1 for r in results if r.get("result") == "captcha")
    err = sum(1 for r in results if r.get("result") == "error")
    lines.append("📊 ✅%s ⏳%s 👮%s ❌%s" % (ok, wait, cap, err))
    lines.append("")
    lines.append("\n\n".join(server_block(r) for r in results))
    return "\n".join(lines)


def main():
    global SERVER_NAME
    if not API_KEY:
        log("❌ 缺少 API_KEY")
        return 1

    log("======================================")
    log(" aclclouds Auto Renew (refer Host-Ship)")
    log("======================================")

    ip = current_ip()
    log("📍 出口IP: %s（代理: %s）" % (ip, "已启用" if PROXY_SERVER else "直连"))

    servers, err = list_servers()
    if servers is None:
        reason = "获取服务器列表失败: %s" % err
        log("❌ " + reason)
        tg("❌ aclclouds 续期异常\n服务器列表获取失败 %s" % err)
        return 1
    if SERVER_IDS:
        servers = [s for s in servers if s["identifier"] in SERVER_IDS]
    if not servers:
        log("❌ 没有可处理服务器")
        tg("❌ aclclouds 续期异常: 没有可处理服务器")
        return 1

    results = [check_server(s["identifier"]) for s in servers]

    report = build_report(results, ip)
    log("——————————")
    # 有实际动作(成功/需人机/异常)才发 TG；纯 wait 只在手动/首次时发一条简报
    has_action = any(r.get("result") in ("success", "captcha", "error") for r in results)
    if MANUAL_RUN and not has_action:
        tg("⏳ <b>aclclouds 检查（手动）</b>\n未到续期窗口，无需操作\n\n" + report)
    elif has_action:
        tg(report)
    else:
        log("⏳ 所有服务器均未到续期窗口，不打扰")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("❌ 运行异常: %s" % exc)
        tg("❌ aclclouds 自动续期异常: %s" % exc)
        sys.exit(1)
