#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright


SERVER_URL = os.getenv("SERVER_URL", "").strip()
HOSTSHIP_LOGIN = os.getenv("HOSTSHIP_LOGIN", "").strip()
HOSTSHIP_PASSWORD = os.getenv("HOSTSHIP_PASSWORD", "").strip()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()

IS_PROXY = os.getenv("IS_PROXY", "false").lower() == "true"
PROXY_SERVER = os.getenv(
    "PROXY_SERVER",
    "socks5://127.0.0.1:1080"
).strip()

MANUAL_RUN = os.getenv(
    "MANUAL_RUN",
    "false"
).lower() == "true"

BJ_TZ = ZoneInfo("Asia/Shanghai")


def log(msg):
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}",
        flush=True
    )


def server_id():
    if not SERVER_URL:
        return "未知"

    return SERVER_URL.rstrip("/").split("/")[-1]


def node_status():
    if IS_PROXY:
        return "✅ 已启用"

    return "⚪ 未启用（直连）"


def get_days(text):
    if not text:
        return None

    match = re.search(
        r"(\d+)\s*Days?",
        text,
        re.I
    )

    if match:
        return int(match.group(1))

    return None


def beijing_now():
    return datetime.now(BJ_TZ)


def estimate_renew_date(status):
    days = get_days(status)

    if days is None:
        return None

    return (
        beijing_now().date()
        + timedelta(days=days)
    )


def tg(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False

    try:
        proxies = None

        if IS_PROXY:
            proxies = {
                "http": PROXY_SERVER,
                "https": PROXY_SERVER,
            }

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
            proxies=proxies,
        )

        if response.ok:
            log("✅ Telegram 通知发送成功")
            return True

        log(
            "❌ Telegram 通知失败: "
            + response.text
        )

        return False

    except Exception as exc:
        log(
            f"❌ Telegram 通知异常: {exc}"
        )
        return False


def current_ip():
    try:
        proxies = None

        if IS_PROXY:
            proxies = {
                "http": PROXY_SERVER,
                "https": PROXY_SERVER,
            }

        response = requests.get(
            "https://api.ipify.org",
            timeout=15,
            proxies=proxies,
        )

        if response.ok:
            return response.text.strip()

        return "获取失败"

    except Exception:
        return "获取失败"


def build_check_message(status, ip):
    days = get_days(status)
    renew_date = estimate_renew_date(status)
    now = beijing_now()

    lines = [
        "⏳ Host-Ship 检查完成",
        "",
        f"🖥️ 服务器：#{server_id()}",
        f"🌐 节点状态：{node_status()}",
        f"📍 出口IP：{ip}",
        f"🕗 检查时间：{now.strftime('%Y/%m/%d %H:%M')}",
        "",
        "🔒 当前状态：未到续期时间",
    ]

    if days is not None:
        lines.append(
            f"⏱️ 距离续期：约 {days} 天"
        )

    if renew_date is not None:
        lines.append(
            "📆 预计可续期："
            + renew_date.strftime("%Y/%m/%d")
        )

    if days is None:
        lines.append(
            f"📅 面板状态：{status}"
        )

    lines.append(
        "⏰ 自动检查：每天 08:00（北京时间）"
    )

    return "\n".join(lines)


def build_success_message(before, after, ip):
    before_days = get_days(before)
    after_days = get_days(after)
    now = beijing_now()

    lines = [
        "🎉 Host-Ship 续期成功",
        "",
        f"🖥️ 服务器：#{server_id()}",
        f"🌐 节点状态：{node_status()}",
        f"📍 出口IP：{ip}",
        f"🕗 续期时间：{now.strftime('%Y/%m/%d %H:%M')}",
        "",
    ]

    if before_days is not None:
        lines.append(
            f"📅 续期前：约 {before_days} 天"
        )
    else:
        lines.append(
            f"📅 续期前：{before}"
        )

    if after_days is not None:
        lines.append(
            f"✅ 续期后：约 {after_days} 天"
        )
    else:
        lines.append(
            f"✅ 续期后：{after}"
        )

    lines.append(
        "⏰ 自动检查：每天 08:00（北京时间）"
    )

    return "\n".join(lines)


def build_error_message(title, reason, ip):
    now = beijing_now()

    return (
        f"{title}\n\n"
        f"🖥️ 服务器：#{server_id()}\n"
        f"🌐 节点状态：{node_status()}\n"
        f"📍 出口IP：{ip}\n"
        f"🕗 检查时间：{now.strftime('%Y/%m/%d %H:%M')}\n\n"
        f"⚠️ 原因：{reason}"
    )


def first_visible(page, selectors):
    for selector in selectors:
        locator = page.locator(
            selector
        ).first

        try:
            if (
                locator.count()
                and locator.is_visible()
            ):
                return locator

        except Exception:
            pass

    return None


def login_if_needed(page):
    page.goto(
        SERVER_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    time.sleep(2)

    body = page.locator(
        "body"
    ).inner_text().lower()

    if (
        "/server/" in page.url
        and "password" not in body
    ):
        return True

    email = first_visible(
        page,
        [
            'input[name="email"]',
            'input[type="email"]',
            'input[name="username"]',
            'input[autocomplete="username"]',
        ],
    )

    password = first_visible(
        page,
        [
            'input[name="password"]',
            'input[type="password"]',
            'input[autocomplete="current-password"]',
        ],
    )

    if not email or not password:
        log(
            "❌ 没找到登录框，"
            f"当前页面: {page.url}"
        )
        return False

    if (
        not HOSTSHIP_LOGIN
        or not HOSTSHIP_PASSWORD
    ):
        log(
            "❌ 缺少 HOSTSHIP_LOGIN "
            "/ HOSTSHIP_PASSWORD"
        )
        return False

    log("🔐 正在登录 Host-Ship...")

    email.fill(HOSTSHIP_LOGIN)
    password.fill(HOSTSHIP_PASSWORD)

    submit = first_visible(
        page,
        [
            'button[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
        ],
    )

    if not submit:
        log("❌ 没找到登录按钮")
        return False

    submit.click()

    page.wait_for_timeout(3000)

    text = page.locator(
        "body"
    ).inner_text().lower()

    challenge_words = [
        "captcha",
        "verify you are human",
        "security check",
        "cloudflare",
    ]

    if any(
        word in text
        for word in challenge_words
    ):
        log(
            "⚠️ 检测到验证码/"
            "安全验证，需要手动处理"
        )
        return False

    page.goto(
        SERVER_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    page.wait_for_timeout(2000)

    return "/server/" in page.url


def get_renewal_text(page):
    text = page.locator(
        "body"
    ).inner_text()

    patterns = [
        r"Renewal\s+in\s+\d+\s+Days?",
        r"Renew\s+in\s+\d+\s+Days?",
        r"\d+\s+Days?\s+until\s+renewal",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.I,
        )

        if match:
            return match.group(0)

    if re.search(
        r"Renew\s+Limit\s+Reached",
        text,
        re.I,
    ):
        return "Renew Limit Reached"

    return "未识别"


def find_renew_button(page):
    candidates = [
        page.get_by_role(
            "button",
            name=re.compile(
                r"^Renew(?:\s+Now|\s+Server)?$",
                re.I,
            ),
        ),
        page.get_by_role(
            "link",
            name=re.compile(
                r"^Renew(?:\s+Now|\s+Server)?$",
                re.I,
            ),
        ),
        page.locator(
            'button:has-text("Renew")'
        ),
        page.locator(
            'a:has-text("Renew")'
        ),
    ]

    for group in candidates:
        try:
            count = group.count()

            for i in range(count):
                item = group.nth(i)

                if item.is_visible():
                    return item

        except Exception:
            pass

    return None


def confirm_if_needed(page):
    page.wait_for_timeout(800)

    dialog = page.locator(
        '[role="dialog"]'
    ).last

    try:
        if (
            not dialog.count()
            or not dialog.is_visible()
        ):
            return

    except Exception:
        return

    buttons = [
        dialog.get_by_role(
            "button",
            name=re.compile(
                r"^Confirm$",
                re.I,
            ),
        ),
        dialog.get_by_role(
            "button",
            name=re.compile(
                r"^Renew$",
                re.I,
            ),
        ),
        dialog.get_by_role(
            "button",
            name=re.compile(
                r"^Yes$",
                re.I,
            ),
        ),
    ]

    for group in buttons:
        try:
            if (
                group.count()
                and group.first.is_visible()
            ):
                group.first.click()
                return

        except Exception:
            pass


def main():
    if not SERVER_URL.startswith(
        "https://panel.host-ship.com/server/"
    ):
        log("❌ SERVER_URL 不正确")

        tg(
            "❌ Host-Ship 配置错误\n"
            "SERVER_URL 不是服务器详情页地址"
        )

        return 1

    log("======================================")
    log(" Host-Ship Free Auto Renew")
    log("======================================")

    log(
        f"🌐 节点状态：{node_status()}"
    )

    ip = current_ip()

    log(
        f"📍 当前出口IP：{ip}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy={
                "server": PROXY_SERVER
            }
            if IS_PROXY
            else None,
            args=[
                "--no-sandbox"
            ],
        )

        context = browser.new_context(
            viewport={
                "width": 1440,
                "height": 1000,
            },
            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        try:
            if not login_if_needed(page):
                page.screenshot(
                    path="hostship_login_fail.png",
                    full_page=True,
                )

                tg(
                    build_error_message(
                        "❌ Host-Ship 登录失败",
                        "登录失败或遇到安全验证",
                        ip,
                    )
                )

                return 1

            log("✅ 登录成功")

            before = get_renewal_text(page)

            log(
                f"📅 当前续期状态：{before}"
            )

            body = page.locator(
                "body"
            ).inner_text()

            if re.search(
                r"Renew\s+Limit\s+Reached",
                body,
                re.I,
            ):
                log(
                    "⏳ 目前未到续期时间，"
                    "不进行操作"
                )

                if MANUAL_RUN:
                    tg(
                        build_check_message(
                            before,
                            ip,
                        )
                    )

                return 0

            button = find_renew_button(page)

            if not button:
                page.screenshot(
                    path="hostship_no_renew_button.png",
                    full_page=True,
                )

                tg(
                    build_error_message(
                        "⚠️ Host-Ship 需要检查",
                        (
                            "没有找到可用的 "
                            f"Renew 按钮；{before}"
                        ),
                        ip,
                    )
                )

                return 1

            try:
                disabled = button.is_disabled()

            except Exception:
                disabled = False

            if disabled:
                log(
                    "⏳ Renew 按钮当前不可点击"
                )

                if MANUAL_RUN:
                    tg(
                        build_check_message(
                            before,
                            ip,
                        )
                    )

                return 0

            log(
                "🔄 已到续期窗口，"
                "点击 Renew..."
            )

            button.click()

            confirm_if_needed(page)

            page.wait_for_timeout(4000)

            after = get_renewal_text(page)

            text_after = page.locator(
                "body"
            ).inner_text()

            success_words = [
                "renewed successfully",
                "renewal successful",
                "successfully renewed",
                "renew limit reached",
            ]

            success = any(
                word in text_after.lower()
                for word in success_words
            )

            if (
                before != "未识别"
                and after != "未识别"
                and after != before
            ):
                success = True

            if success:
                log(
                    "✅ 续期成功："
                    f"{before} -> {after}"
                )

                tg(
                    build_success_message(
                        before,
                        after,
                        ip,
                    )
                )

                return 0

            page.screenshot(
                path="hostship_renew_uncertain.png",
                full_page=True,
            )

            log(
                "⚠️ 已点击续期，"
                "但无法确认结果"
            )

            tg(
                build_error_message(
                    "⚠️ Host-Ship 续期结果需要检查",
                    (
                        f"续期前：{before}；"
                        f"续期后：{after}"
                    ),
                    ip,
                )
            )

            return 1

        except Exception as exc:
            log(
                f"❌ 运行异常：{exc}"
            )

            try:
                page.screenshot(
                    path="hostship_error.png",
                    full_page=True,
                )
            except Exception:
                pass

            tg(
                build_error_message(
                    "❌ Host-Ship 自动续期异常",
                    str(exc),
                    ip,
                )
            )

            return 1

        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
