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


# 面板首页地址（硬编码，无需配置）
PANEL_URL = "https://panel.host-ship.com/"
# 服务器详情页地址（运行期自动获取：点击 MANAGE SERVER 进入 /server/ 后记录）
SERVER_URL = ""
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

# 截图模式（三档）：
# - auto（默认）：成功/未到窗口只发文字；失败时发 1 张当前页截图。
# - steps：保持分步截图（登录后 5 张按顺序发 TG），用于调试。
# - off：完全不截图（也不发 TG 图）；失败截图仍存文件走 Artifact。
# Secrets 里 SEND_SHOTS=true 视为 steps（兼容旧开关）。
def _shots_mode():
    v = os.getenv("SEND_SHOTS", "").strip().lower()
    if v in ("true", "1", "yes", "on", "steps", "debug", "verbose"):
        return "steps"
    if v in ("false", "0", "no", "off", "none"):
        return "off"
    return "auto"


SHOTS_MODE = _shots_mode()
SEND_SHOTS = SHOTS_MODE == "steps"

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


TG_CAPTION_MAX = 900

# 登录后关键步骤截图（按顺序），全部发 TG 供人工检查。
# 只在登录成功后采集；登录失败只有 1 张登录页截图。
STEP_SHOTS = [
    ("logged_in", "1️⃣ 登录后-服务器页"),
    ("before_renew", "2️⃣ 点击Renew前-状态页"),
    ("confirm_dialog", "3️⃣ 确认对话框"),
    ("after_renew_now", "4️⃣ 点击Renew now后"),
    ("final", "5️⃣ 最终结果页"),
]


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


def tg_photo(path, caption="", force=False):
    """发一张截图到 TG。返回是否成功。

    steps/off 模式由调用方经 snap/send_step_shots 控制；
    auto 模式失败截图传 force=True 绕过开关。
    """
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过截图")
        return False

    if not force and not SEND_SHOTS:
        return False

    try:
        proxies = None

        if IS_PROXY:
            proxies = {
                "http": PROXY_SERVER,
                "https": PROXY_SERVER,
            }

        with open(path, "rb") as f:
            response = requests.post(
                (
                    "https://api.telegram.org/"
                    f"bot{TG_BOT_TOKEN}/sendPhoto"
                ),
                data={
                    "chat_id": TG_CHAT_ID,
                    "caption": caption[:TG_CAPTION_MAX],
                },
                files={
                    "photo": (
                        os.path.basename(path),
                        f,
                        "image/png",
                    ),
                },
                timeout=60,
                proxies=proxies,
            )

        if response.ok:
            log(f"✅ TG 截图已发送：{caption}")
            return True

        log(
            "❌ TG 截图发送失败: "
            + response.text[:200]
        )
        return False

    except Exception as exc:
        log(f"❌ TG 截图发送异常: {exc}")
        return False


def snap(page, name, force=False):
    """截视口图并返回路径。

    steps 模式：存 hostship_step_序号_名称.png。
    auto 模式：force=False 时跳过；失败截图传 force=True，
    存 hostship_fail_名称.png（发 TG + Artifact）。
    off 模式：snap(force=False) 跳过；send_fail_shot 只存文件。
    """
    if not SEND_SHOTS and not force:
        return None

    if force and not SEND_SHOTS:
        path = f"hostship_fail_{name}.png"
    else:
        idx = next(
            (
                i
                for i, (k, _) in enumerate(STEP_SHOTS)
                if k == name
            ),
            99,
        )
        path = f"hostship_step_{idx}_{name}.png"

    try:
        page.screenshot(
            path=path,
            full_page=False,
        )
    except Exception as exc:
        log(f"截图失败 {name}：{exc}")
        return None

    return path


def send_step_shots(collected):
    """把已采集的步骤截图按顺序发 TG（steps 模式专用）。"""
    if not SEND_SHOTS:
        return

    for name, path in collected:
        label = next(
            (t for k, t in STEP_SHOTS if k == name),
            name,
        )
        tg_photo(path, f"🖥️ #{server_id()} {label}")


def send_fail_shot(page, name, caption):
    """auto 模式失败截图：存文件，发 TG（force 绕过开关），走 Artifact。

    off 模式：只存文件走 Artifact，不发 TG。
    """
    if SHOTS_MODE == "off":
        try:
            page.screenshot(path=f"hostship_fail_{name}.png", full_page=False)
        except Exception:
            pass
        return

    path = snap(page, name, force=True)
    if path:
        tg_photo(path, caption, force=True)


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


def goto_panel(page):
    """打开面板首页。返回 True 表示已在登录态（能看到账号信息/服务列表）。"""
    page.goto(
        PANEL_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    time.sleep(2)

    try:
        body = page.locator(
            "body"
        ).inner_text().lower()
    except Exception:
        return False

    login_markers = [
        'input[name="email"]',
        'input[type="email"]',
        'input[name="username"]',
        'input[type="password"]',
    ]

    for sel in login_markers:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                return False
        except Exception:
            pass

    # 没有登录框，且 body 里出现已登录才有的元素（服务卡片/登出等）
    for marker in ("manage server", "logout", "sign out", "my service"):
        if marker in body:
            return True

    # 兜底：不在登录页也算可能已登录，交给后续 MANAGE SERVER 检查
    return "password" not in body


def find_manage_server(page):
    """在面板首页底部找 MANAGE SERVER 按钮/链接，返回 locator，找不到返回 None。"""
    candidates = [
        page.get_by_role(
            "button",
            name=re.compile(r"manage\s*server", re.I),
        ),
        page.get_by_role(
            "link",
            name=re.compile(r"manage\s*server", re.I),
        ),
        page.locator('button:has-text("MANAGE SERVER")'),
        page.locator('a:has-text("MANAGE SERVER")'),
        page.locator(':text("MANAGE SERVER")'),
    ]

    for group in candidates:
        try:
            count = group.count()

            for i in range(count):
                item = group.nth(i)

                try:
                    if item.is_visible():
                        return item
                except Exception:
                    continue

        except Exception:
            pass

    return None


def goto_server_from_panel(page, timeout_ms=15000):
    """已在面板首页登录态：找 MANAGE SERVER 并点进去，返回 True/False。

    有按钮：滚动到底部确保可见，点击后等待跳到 /server/ 详情页，
    并把详情页 URL 记录到全局 SERVER_URL（供通知展示用）。
    无按钮：说明账号下没有服务，直接返回 False 走失败流程。
    """
    global SERVER_URL
    manage = find_manage_server(page)

    if not manage:
        # 页面可能还没加载完/懒加载在底部，再滚到底等一会儿重试一次
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

        page.wait_for_timeout(3000)
        manage = find_manage_server(page)

    if not manage:
        log("❌ 面板首页未找到 MANAGE SERVER，账号下可能没有服务")
        return False

    log("✅ 找到 MANAGE SERVER，点击进入服务器页...")

    try:
        manage.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    try:
        manage.click(timeout=10000)
    except Exception as exc:
        log(f"⚠️ MANAGE SERVER 常规点击无效，换 JS 直点：{exc}")
        try:
            manage.evaluate("el => el.click()")
        except Exception as exc2:
            log(f"❌ MANAGE SERVER 点击失败：{exc2}")
            return False

    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        try:
            if "/server/" in page.url:
                page.wait_for_timeout(2000)
                SERVER_URL = page.url
                log(f"🖥 服务器详情页：{SERVER_URL}")
                return True
        except Exception:
            pass

        page.wait_for_timeout(1000)

    # 点击后没跳到详情页：可能是新开标签页
    try:
        if len(page.context.pages) > 1:
            for p in page.context.pages:
                try:
                    if "/server/" in p.url:
                        page = p
                        page.wait_for_timeout(2000)
                        SERVER_URL = p.url
                        log(f"🖥 服务器详情页：{SERVER_URL}")
                        return True
                except Exception:
                    continue
    except Exception:
        pass

    log(f"❌ 点击 MANAGE SERVER 后未进入服务器页，当前: {page.url}")
    return False


def login_if_needed(page):
    # 先走面板首页：已登录直接返回 True，未登录才填账号密码
    if goto_panel(page):
        log("✅ 面板已是登录态")
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

    # 登录后回到面板首页确认登录态
    if not goto_panel(page):
        log("❌ 登录后仍不在面板首页")
        return False

    return True


RENEWAL_PATTERNS = [
    r"Renewal\s+in\s+\d+\s+Days?",
    r"Renew\s+in\s+\d+\s+Days?",
    r"\d+\s+Days?\s+until\s+renewal",
]


def parse_renewal_text(text):
    for pattern in RENEWAL_PATTERNS:
        match = re.search(
            pattern,
            text or "",
            re.I,
        )

        if match:
            return re.sub(
                r"\s+",
                " ",
                match.group(0),
            ).strip()

    if re.search(
        r"Renew\s+Limit\s+Reached",
        text or "",
        re.I,
    ):
        return "Renew Limit Reached"

    return "未识别"


def get_renewal_text(page):
    text = page.locator(
        "body"
    ).inner_text()

    return parse_renewal_text(text)


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


def wait_dialog(page, timeout_ms=10000):
    """等确认对话框出现，返回 dialog locator，超时返回 None。

    判活条件（任一即算出现，对齐上游做法）：
    1. [role="dialog"] 可见（Radix/HeadlessUI 标准弹窗）
    2. 可见文本含 "Confirm server renewal"（SweetAlert/自定义弹窗，
       可能没有 role=dialog，上游用此标题定位）
    """
    deadline = time.monotonic() + timeout_ms / 1000

    title_re = re.compile(
        r"Confirm\s+server\s+renewal",
        re.I,
    )

    while time.monotonic() < deadline:
        try:
            dialog = page.locator(
                '[role="dialog"]'
            ).last

            if (
                dialog.count()
                and dialog.is_visible()
            ):
                return dialog

        except Exception:
            pass

        try:
            title = page.get_by_text(title_re).first

            if (
                title.count()
                and title.is_visible()
            ):
                # 用标题最近的弹窗容器；没有则退回整个 dialog 作用域
                container = title.locator(
                    'xpath=ancestor-or-self::*[@role="dialog"][1]'
                )

                try:
                    if (
                        container.count()
                        and container.first.is_visible()
                    ):
                        return container.first

                except Exception:
                    pass

                return page.locator("body")

        except Exception:
            pass

        page.wait_for_timeout(500)

    return None


def click_renew_now(page, dialog):
    """在确认对话框里点 Renew now，返回是否点过。

    作用域(dialog)可能是 body 兜底，因此候选同步上游做法：
    dialog 内优先，全页兜底。上游用 is_visible + is_enabled
    双检查后直接点击；此处再加 expect_response 监听后端请求，
    没抓到请求说明没点上（按钮 disabled / 被遮挡等）。
    """
    candidates = [
        dialog.get_by_role(
            "button",
            name=re.compile(
                r"^Renew\s*now$",
                re.I,
            ),
        ),
        page.get_by_role(
            "button",
            name=re.compile(
                r"^Renew\s*now$",
                re.I,
            ),
        ),
        dialog.locator(
            'button:has-text("Renew now")'
        ),
        page.locator(
            'button:has-text("Renew now")'
        ),
    ]

    target = None

    for group in candidates:
        try:
            for i in range(group.count()):
                item = group.nth(i)

                try:
                    if not item.is_visible():
                        continue
                except Exception:
                    continue

                try:
                    if not item.is_enabled():
                        continue
                except Exception:
                    try:
                        if item.is_disabled():
                            continue
                    except Exception:
                        pass

                name = (
                    item.inner_text()
                    or ""
                ).strip()

                if re.search(
                    r"cancel|close",
                    name,
                    re.I,
                ):
                    continue

                target = item
                break

        except Exception:
            pass

        if target is not None:
            break

    if target is None:
        return False

    try:
        name = (
            target.inner_text()
            or ""
        ).strip()

    except Exception:
        name = "Renew now"

    log(
        "🖱️ 点击确认按钮："
        f"{name}"
    )

    # 先滚动到按钮并等可点击，避免点偏/被遮挡
    try:
        target.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    try:
        target.wait_for(state="visible", timeout=5000)
    except Exception:
        pass

    clicked = False
    click_error = None

    # 策略1：Playwright 点击（带 expect_response 监听后端请求）
    try:
        with page.expect_response(
            lambda r: r.request.method in ("POST", "PUT", "PATCH")
            and r.status < 500,
            timeout=10000,
        ) as resp_info:
            target.click(timeout=5000)

        log(
            "📡 续期请求已发出："
            f"{resp_info.value.status} "
            f"{resp_info.value.url[:120]}"
        )

        return True

    except Exception as exc:
        click_error = exc
        log(f"⚠️ 常规点击无效，换 JS 直点：{exc}")

    # 策略2：JS dispatchEvent 直点（绕过 actionability 检查）
    try:
        target.evaluate("el => el.click()")
        clicked = True
        log("🖱️ 已用 JS dispatch 点击 Renew now")
    except Exception as exc:
        log(f"❌ JS 点击也失败：{exc}")
        return False

    # 策略2 点完后：等对话框消失 或 后端请求 或 天数变化
    try:
        dialog_hidden = False
        try:
            dialog.wait_for(state="hidden", timeout=8000)
            dialog_hidden = True
        except Exception:
            pass

        if dialog_hidden:
            log("✅ 确认对话框已消失，Renew now 已生效")
            return True

        # 对话框没消失：再看有没有后端请求
        try:
            with page.expect_response(
                lambda r: r.request.method in ("POST", "PUT", "PATCH")
                and r.status < 500,
                timeout=5000,
            ) as resp_info:
                pass

            log(
                "📡 续期请求已发出："
                f"{resp_info.value.status} "
                f"{resp_info.value.url[:120]}"
            )
            return True
        except Exception:
            pass

        log(
            "⚠️ JS 点击后对话框仍在且无后端请求 "
            f"(首次点击异常：{click_error})"
        )
        return False

    except Exception as exc:
        log(f"⚠️ JS 点击后确认异常：{exc}")
        return clicked


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
            return False

    except Exception:
        return False

    return click_renew_now(page, dialog)


SUCCESS_WORDS = [
    "renewed successfully",
    "renewal successful",
    "successfully renewed",
    "renew limit reached",
]


def wait_renew_result(page, before, timeout_ms=15000):
    """Renew now 已点后短轮询确认结果（最多 15s，不耗额度）。

    注意：调用本函数前必须已点过对话框里的 Renew now，
    本函数只做结果确认，不再点击任何确认按钮。
    """
    deadline = time.monotonic() + timeout_ms / 1000

    after = get_renewal_text(page)
    last_text = ""

    while time.monotonic() < deadline:
        page.wait_for_timeout(2000)

        after = get_renewal_text(page)

        try:
            last_text = page.locator(
                "body"
            ).inner_text()

        except Exception:
            last_text = ""

        lowered = (last_text or "").lower()

        if any(
            word in lowered
            for word in SUCCESS_WORDS
        ):
            return {
                "after": after,
                "success": True,
                "reason": "success_word",
            }

        if (
            before != "未识别"
            and after != "未识别"
            and after != before
        ):
            return {
                "after": after,
                "success": True,
                "reason": "days_changed",
            }

        if re.search(
            r"Renew\s+Limit\s+Reached",
            last_text or "",
            re.I,
        ):
            return {
                "after": "Renew Limit Reached",
                "success": True,
                "reason": "limit_reached",
            }

    return {
        "after": after,
        "success": False,
        "reason": "timeout",
    }


def main():
    if (
        not HOSTSHIP_LOGIN
        or not HOSTSHIP_PASSWORD
    ):
        log("❌ 缺少 HOSTSHIP_LOGIN / HOSTSHIP_PASSWORD")

        tg(
            "❌ Host-Ship 配置错误\n"
            "缺少 HOSTSHIP_LOGIN / HOSTSHIP_PASSWORD"
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
        shots = []

        def take(name):
            path = snap(page, name)
            if path:
                shots.append((name, path))

        def finish(result_text, fail_shot=None, fail_caption=""):
            """先发文字结果，再发截图。

            steps 模式：按顺序发分步截图。
            auto 模式：只发 fail_shot 那 1 张（force 绕过开关）。
            off 模式：fail_shot 仅存文件走 Artifact，不发 TG。
            """
            tg(result_text)
            if SHOTS_MODE == "steps":
                send_step_shots(shots)
            elif fail_shot and SHOTS_MODE == "auto":
                tg_photo(fail_shot, fail_caption, force=True)

        try:
            if not login_if_needed(page):
                msg = build_error_message(
                    "❌ Host-Ship 登录失败",
                    "登录失败或遇到安全验证",
                    ip,
                )
                tg(msg)
                send_fail_shot(page, "login", f"🖥️ #{server_id()} 登录失败页")

                return 1

            log("✅ 登录成功")
            take("logged_in")

            # 面板首页检查：底部有 MANAGE SERVER 才说明账号下有服务，
            # 点进去到服务器详情页再找续期按钮；没有则直接走失败流程。
            if not goto_server_from_panel(page):
                take("before_renew")
                fail_shot = snap(page, "no_manage_server", force=True)

                finish(
                    build_error_message(
                        "❌ Host-Ship 面板无服务",
                        (
                            "登录后面板首页未找到 "
                            "MANAGE SERVER 按钮，"
                            "账号下可能没有服务"
                        ),
                        ip,
                    ),
                    fail_shot=fail_shot,
                    fail_caption=f"🖥️ #{server_id()} 面板无 MANAGE SERVER",
                )

                return 1

            log("✅ 已进入服务器详情页")
            take("logged_in")

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
                take("before_renew")

                if MANUAL_RUN:
                    finish(
                        build_check_message(
                            before,
                            ip,
                        )
                    )
                else:
                    send_step_shots(shots)

                return 0

            button = find_renew_button(page)

            if not button:
                take("before_renew")
                fail_shot = snap(page, "no_renew_button", force=True)

                finish(
                    build_error_message(
                        "⚠️ Host-Ship 需要检查",
                        (
                            "没有找到可用的 "
                            f"Renew 按钮；{before}"
                        ),
                        ip,
                    ),
                    fail_shot=fail_shot,
                    fail_caption=f"🖥️ #{server_id()} 无 Renew 按钮",
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
                take("before_renew")

                if MANUAL_RUN:
                    finish(
                        build_check_message(
                            before,
                            ip,
                        )
                    )
                else:
                    send_step_shots(shots)

                return 0

            log(
                "🔄 已到续期窗口，"
                "点击 Renew..."
            )
            take("before_renew")

            button.click()

            # 等确认对话框弹出（最多 10s），再点 Renew now。
            # 只有点过 Renew now 才算真正续期。
            dialog = wait_dialog(page, timeout_ms=10000)
            take("confirm_dialog")

            if not dialog:
                fail_shot = snap(page, "no_confirm_dialog", force=True)

                finish(
                    build_error_message(
                        "⚠️ Host-Ship 需要检查",
                        (
                            "点击 Renew 后未弹出确认对话框，"
                            f"当前状态：{before}"
                        ),
                        ip,
                    ),
                    fail_shot=fail_shot,
                    fail_caption=f"🖥️ #{server_id()} 无确认对话框",
            )

                return 1

            try:
                title = (
                    dialog.inner_text()
                    or ""
                )[:200].replace("\n", " | ")

                log(f"📋 确认对话框内容：{title}")

            except Exception:
                pass

            # 用 expect_response 监听续期请求，避免“点了但不知道
            # 有没有发出去”：若 Renew now 没触发任何请求，直接报错。
            clicked = click_renew_now(page, dialog)
            take("after_renew_now")

            if not clicked:
                fail_shot = snap(page, "confirm_unknown", force=True)

                finish(
                    build_error_message(
                        "⚠️ Host-Ship 需要检查",
                        (
                            "确认对话框出现但没找到 "
                            "Renew now 按钮；"
                            f"当前状态：{before}"
                        ),
                        ip,
                    ),
                    fail_shot=fail_shot,
                    fail_caption=f"🖥️ #{server_id()} 确认对话框",
            )

                return 1

            result = wait_renew_result(
                page,
                before,
                timeout_ms=15000,
            )
            take("final")

            after = result["after"]

            if result["success"]:
                log(
                    "✅ 续期成功："
                    f"{before} -> {after} "
                    f"(确认:{result['reason']})"
                )

                finish(
                    build_success_message(
                        before,
                        after,
                        ip,
                    )
                )

                return 0

            fail_shot = snap(page, "renew_uncertain", force=True)

            log(
                "⚠️ 已点击续期，"
                "但无法确认结果"
            )

            finish(
                build_error_message(
                    "⚠️ Host-Ship 续期结果需要检查",
                    (
                        f"续期前：{before}；"
                        f"续期后：{after}；"
                        "已等待 15s 仍无成功标识，"
                        "请人工到面板确认"
                    ),
                    ip,
                ),
                fail_shot=fail_shot,
                fail_caption=f"🖥️ #{server_id()} 结果未确认",
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
            try:
                page.screenshot(path="hostship_fail_error.png", full_page=False)
            except Exception:
                pass
            if SHOTS_MODE == "steps":
                send_step_shots(shots)
                tg_photo("hostship_fail_error.png", f"🖥️ #{server_id()} 异常页", force=True)
            elif SHOTS_MODE == "auto":
                tg_photo("hostship_fail_error.png", f"🖥️ #{server_id()} 异常页", force=True)

            return 1

        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
