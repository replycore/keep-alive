#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Host-Ship 自动续期（API 版）

直接调用面板续期接口，不再用 Playwright 模拟点击：
    POST {PANEL_URL}/api/client/servers/{SERVER_ID}/renew
    Authorization: Bearer {API_KEY}   # ptlc_ 开头的 Client API Key

认证：面板 Account -> API Keys 创建 Client Key（ptlc_）。
服务器 ID：续期请求 URL / 服务器详情页 /server/<id> 里可见。

行为与旧版一致：
- 达到 30 天续期上限（响应含 "cannot add more than" 等）即停；
- 多次续期叠加（RENEW_INTERVAL 秒间隔，最多 MAX_ATTEMPTS 次）；
- TG 通知（成功/失败必发；"已达上限"仅 MANUAL_RUN 时发，定时任务不打扰）；
- 支持 socks 代理（IS_PROXY / PROXY_SERVER），浏览器换成 requests 后代理仍然生效。
"""

import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# 面板地址（默认硬编码，可用环境变量覆盖，便于测试）
PANEL_URL = os.getenv(
    "HOSTSHIP_PANEL_URL",
    "https://panel.host-ship.com",
).rstrip("/")

SERVER_ID = os.getenv("HOSTSHIP_SERVER_ID", "").strip()
API_KEY = os.getenv("HOSTSHIP_API_KEY", "").strip()

# 请求间隔（秒）与最大请求次数：面板对连续续期有冷却，61 秒实测可用
RENEW_INTERVAL = int(os.getenv("RENEW_INTERVAL", "61"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "5"))

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()

IS_PROXY = os.getenv("IS_PROXY", "false").lower() == "true"
PROXY_SERVER = os.getenv(
    "PROXY_SERVER",
    "socks5://127.0.0.1:1080",
).strip()

MANUAL_RUN = os.getenv(
    "MANUAL_RUN",
    "false",
).lower() == "true"

try:
    BJ_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    # 极简环境（无 tzdata）兜底：直接用 UTC+8
    from datetime import timezone
    BJ_TZ = timezone(timedelta(hours=8), name="CST")

# 续期上限的响应文案（宽松匹配，防止后端文案微调漏检）
LIMIT_PATTERNS = (
    "cannot add more than",
    "renew limit reached",
)


def log(msg):
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}",
        flush=True,
    )


def beijing_now():
    return datetime.now(BJ_TZ)


def request_proxies():
    if IS_PROXY:
        return {
            "http": PROXY_SERVER,
            "https": PROXY_SERVER,
        }

    return None


def current_ip():
    try:
        response = requests.get(
            "https://api.ipify.org",
            timeout=15,
            proxies=request_proxies(),
        )

        if response.ok:
            return response.text.strip()

        return "获取失败"

    except Exception:
        return "获取失败"


def tg(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False

    try:
        response = requests.post(
            (
                "https://api.telegram.org/"
                f"bot{TG_BOT_TOKEN}/sendMessage"
            ),
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
            },
            timeout=20,
            proxies=request_proxies(),
        )

        if response.ok:
            log("✅ Telegram 通知发送成功")
            return True

        log("❌ Telegram 通知失败: " + response.text[:200])
        return False

    except Exception as exc:
        log(f"❌ Telegram 通知异常: {exc}")
        return False


def is_limit_reached(body):
    lowered = (body or "").lower()

    return any(pattern in lowered for pattern in LIMIT_PATTERNS)


def renew_once():
    """发起一次续期请求。

    返回 (kind, detail)，kind 取值：
    - "success"   续期成功（HTTP 200/204）
    - "limit"     已达续期上限（再续就超 30 天）
    - "auth"      鉴权/资源错误（401/403/404），重试无意义
    - "fail"      其他失败（网络异常或非预期状态码）
    """
    url = (
        f"{PANEL_URL}/api/client/servers/"
        f"{SERVER_ID}/renew"
    )

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=HTTP_TIMEOUT,
            proxies=request_proxies(),
        )

    except requests.RequestException as exc:
        log(f"❌ 请求异常：{exc}")
        return "fail", f"网络异常：{exc}"

    body = response.text.strip()
    short = body[:300] if body else "<空响应>"
    log(f"HTTP {response.status_code} | {short}")

    # 先判上限：后端可能用 4xx 表达"天数将超 30 天"
    if is_limit_reached(body):
        return "limit", body

    if response.status_code in (200, 204):
        return "success", body

    if response.status_code in (401, 403):
        return "auth", (
            f"鉴权失败 HTTP {response.status_code}，"
            "请检查 API_KEY 是否有效/过期"
        )

    if response.status_code == 404:
        return "auth", (
            "HTTP 404，请检查 SERVER_ID "
            "是否正确、API Key 是否属于同一账号"
        )

    return "fail", f"HTTP {response.status_code}"


def build_header(ip):
    now = beijing_now()

    return [
        "🖥️ Host-Ship 自动续期（API 模式）",
        "",
        f"🆔 服务器ID：{SERVER_ID}",
        f"📍 出口IP：{ip}",
        f"🕗 时间：{now.strftime('%Y/%m/%d %H:%M')}",
        "",
    ]


def main():
    if not SERVER_ID or not API_KEY:
        log("❌ 缺少 HOSTSHIP_SERVER_ID / HOSTSHIP_API_KEY")
        tg(
            "❌ Host-Ship 配置错误\n"
            "缺少 HOSTSHIP_SERVER_ID / HOSTSHIP_API_KEY"
        )
        return 1

    if API_KEY.startswith("ptlc_xxxx"):
        log("❌ API_KEY 还是占位符，请填真实 Client API Key")
        tg("❌ Host-Ship 配置错误：API_KEY 仍是占位符")
        return 1

    log("======================================")
    log(" Host-Ship Free Auto Renew (API)")
    log(f" 面板: {PANEL_URL}")
    log(f" 服务器ID: {SERVER_ID}")
    log(f" 间隔: {RENEW_INTERVAL}s × {MAX_ATTEMPTS} 次")
    log(f" 代理: {PROXY_SERVER if IS_PROXY else '直连'}")
    log("======================================")

    ip = current_ip()
    log(f"📍 当前出口IP：{ip}")

    success_count = 0
    fail_count = 0
    last_detail = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(
            f"----- 第 {attempt}/{MAX_ATTEMPTS} 次请求 "
            f"@ {beijing_now().strftime('%H:%M:%S')} -----"
        )

        kind, detail = renew_once()
        last_detail = detail

        if kind == "limit":
            log("⏳ 已出现续期上限提示，停止请求")

            msg = build_header(ip) + [
                "🔒 已达续期上限（30 天）",
            ]

            if success_count:
                msg.append(f"✅ 本次已成功续期 {success_count} 次")

            if MANUAL_RUN:
                # 定时任务：已达上限属常态，不打扰
                tg("\n".join(msg))
            else:
                log("（定时运行，跳过'已达上限'通知）")

            return 0

        if kind == "success":
            success_count += 1
            log(f"✅ 第 {attempt} 次续期成功")

        elif kind == "auth":
            log(f"❌ {detail}")
            tg(
                "\n".join(
                    build_header(ip)
                    + [
                        "❌ 续期失败",
                        f"⚠️ 原因：{detail}",
                    ]
                )
            )
            return 1

        else:
            fail_count += 1
            log(f"❌ 第 {attempt} 次续期失败：{detail}")

        if attempt < MAX_ATTEMPTS:
            log(f"[*] 等待 {RENEW_INTERVAL} 秒后继续...")
            time.sleep(RENEW_INTERVAL)

    # 跑完所有次数仍未命中上限
    if success_count:
        log(f"🎉 共成功续期 {success_count} 次")
        tg(
            "\n".join(
                build_header(ip)
                + [
                    f"🎉 续期成功 {success_count} 次",
                    f"❌ 失败 {fail_count} 次",
                    "",
                    "ℹ️ 未出现续期上限提示，"
                    "下次定时任务将继续尝试",
                ]
            )
        )
        return 0

    log("❌ 全部请求失败")
    tg(
        "\n".join(
            build_header(ip)
            + [
                "❌ 续期失败（0 次成功）",
                f"⚠️ 原因：{last_detail[:200]}",
            ]
        )
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
