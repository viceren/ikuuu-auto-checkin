"""
ikuuu Cookie 刷新工具 — 半自动模式

工作流程:
  1. 从 config.json 读取邮箱和密码
  2. 打开 Chromium 浏览器（可见窗口），导航到 ikuuu 登录页
  3. 自动填入邮箱和密码
  4. 用户在浏览器中手动完成人机验证并点击登录
  5. 脚本检测到登录成功后自动提取 Cookie
  6. 保存到 config.json，更新 checkin.py 可用的 Cookie

依赖: playwright (pip install playwright)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

# ── 常量 ──────────────────────────────────────────────────
BASE_URL = os.environ.get("IKUUU_BASE_URL", "https://ikuuu.win")
LOGIN_URL = f"{BASE_URL}/auth/login"
USER_URL = f"{BASE_URL}/user"
CHECKIN_URL = f"{BASE_URL}/user/checkin"
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

LOGIN_TIMEOUT = int(os.environ.get("IKUUU_LOGIN_TIMEOUT", "300"))  # 5 分钟

# ── 日志 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("refresh_cookie")


# ═══════════════════════════════════════════════════════════
#  配置读写
# ═══════════════════════════════════════════════════════════


def load_config() -> dict:
    """从 config.json 加载配置，失败时返回空字典。"""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取 config.json 失败: %s", e)
    return {}


def save_config(config: dict) -> None:
    """将配置字典写回 config.json。"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    logger.info("✓ 配置已保存到 %s", CONFIG_PATH)


# ═══════════════════════════════════════════════════════════
#  Cookie 工具
# ═══════════════════════════════════════════════════════════


def cookies_to_str(cookies: list[dict]) -> str:
    """Playwright cookie 列表 → `name1=value1; name2=value2` 字符串。"""
    return "; ".join(f'{c["name"]}={c["value"]}' for c in cookies)


def cookies_to_dict(cookies: list[dict]) -> dict[str, str]:
    """Playwright cookie 列表 → {name: value} 字典。"""
    return {c["name"]: c["value"] for c in cookies}


# ═══════════════════════════════════════════════════════════
#  页面检测
# ═══════════════════════════════════════════════════════════


async def is_on_login_page(page: Page) -> bool:
    """判断当前是否在登录页（可能含 Cloudflare 中间页）。"""
    url = page.url
    if "/auth/login" in url:
        return True
    # 有时 Cloudflare 检查后才会到登录页
    email_input = await page.query_selector(
        'input[name="email"], input[type="email"], input[id="email"]'
    )
    return email_input is not None


async def is_logged_in(page: Page) -> bool:
    """判断当前是否已登录（URL 不在登录页，且无登录表单）。"""
    url = page.url
    if (
        "/user" in url
        or "/shop" in url
        or "/node" in url
    ):
        return True
    if "/auth/login" in url:
        # 虽然在登录页，但检查是否已经重定向出去了
        # （有些站点登录后不会立即跳转）
        pass
    # 检查登录表单是否存在
    form = await page.query_selector(
        'input[name="email"], input[type="email"], input[id="email"]'
    )
    return form is None


async def detect_captcha(page: Page) -> str | None:
    """检测页面上的验证码类型，返回名称或 None。"""
    checks = [
        ('.cf-turnstile', "Cloudflare Turnstile"),
        ('iframe[src*="turnstile"]', "Cloudflare Turnstile (iframe)"),
        ('iframe[src*="recaptcha"]', "Google reCAPTCHA"),
        ('.g-recaptcha', "Google reCAPTCHA"),
        ('iframe[src*="hcaptcha"]', "hCaptcha"),
        ('.h-captcha', "hCaptcha"),
        ('#captcha', "Generic CAPTCHA"),
        ('[class*="captcha"]', "未知验证码"),
    ]
    for selector, name in checks:
        el = await page.query_selector(selector)
        if el and await el.is_visible():
            return name
    return None


async def wait_for_cloudflare(page: Page) -> None:
    """等待 Cloudflare「正在检查浏览器」页面完成。"""
    for _ in range(30):  # 最多 30 秒
        cf_check = await page.query_selector("#cf-please-wait, .cf-browser-verification, #challenge-form")
        if not cf_check:
            return
        await asyncio.sleep(1)


# ═══════════════════════════════════════════════════════════
#  登录流程
# ═══════════════════════════════════════════════════════════


async def fill_login_form(page: Page, email: str, password: str) -> bool:
    """在登录页填入邮箱和密码。返回是否找到表单。"""
    # 邮箱
    email_selectors = [
        'input[name="email"]',
        'input[type="email"]',
        'input[id="email"]',
        'input[placeholder*="邮箱"]',
        'input[placeholder*="email"]',
        'input[placeholder*="Email"]',
    ]
    email_input = None
    for sel in email_selectors:
        email_input = await page.query_selector(sel)
        if email_input:
            break

    if not email_input:
        logger.error("未找到邮箱输入框，页面结构可能已变化")
        return False

    await email_input.click()
    await asyncio.sleep(0.3)
    await email_input.fill(email)
    logger.info("已填入邮箱: %s", email)

    # 密码
    pwd_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[id="password"]',
        'input[placeholder*="密码"]',
        'input[placeholder*="password"]',
    ]
    pwd_input = None
    for sel in pwd_selectors:
        pwd_input = await page.query_selector(sel)
        if pwd_input:
            break

    if pwd_input:
        await pwd_input.click()
        await asyncio.sleep(0.3)
        await pwd_input.fill(password)
        logger.info("已填入密码")
    else:
        logger.warning("未找到密码输入框")

    return True


async def try_click_login(page: Page) -> bool:
    """尝试自动点击登录按钮。返回是否成功找到按钮。"""
    button_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("登录")',
        'button:has-text("Login")',
        'button:has-text("登 录")',
        'button:has-text("Sign in")',
        'button.btn-primary',
        '[class*="login"] button',
    ]
    for sel in button_selectors:
        btn = await page.query_selector(sel)
        if btn:
            try:
                await btn.click(timeout=3000)
                logger.info("已自动点击登录按钮")
                return True
            except Exception:
                continue
    return False


async def wait_for_login_success(
    page: Page, timeout: int = LOGIN_TIMEOUT
) -> bool:
    """轮询等待用户完成登录。检测到已登录状态返回 True，超时返回 False。"""
    start = asyncio.get_event_loop().time()
    logger.info("等待登录完成（最多 %d 秒）...", timeout)

    while (asyncio.get_event_loop().time() - start) < timeout:
        logged_in = await is_logged_in(page)
        if logged_in:
            return True
        await asyncio.sleep(1.5)

    return False


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════


async def refresh_cookie() -> int:
    """主 Cookie 刷新流程。返回 0 表示成功，非 0 表示失败。"""
    print()
    print("=" * 56)
    print("   ikuuu Cookie 刷新工具 — 半自动模式")
    print("=" * 56)
    print()

    # ── 1. 读取配置 ──
    config = load_config()
    email = config.get("email", "") or os.environ.get("IKUUU_EMAIL", "")
    password = config.get("password", "") or os.environ.get("IKUUU_PASSWORD", "")

    if not email:
        logger.error("未配置邮箱")
        logger.error(
            "请在 config.json 中添加 email 字段，"
            "或设置 IKUUU_EMAIL 环境变量"
        )
        return 1

    if not password:
        logger.warning(
            "未在配置或环境变量中找到密码，将仅填入邮箱"
        )

    # ── 2. 启动浏览器 ──
    logger.info("正在启动 Chromium 浏览器...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
        )
        page = await context.new_page()

        # ── 3. 访问登录页 ──
        logger.info("正在访问 %s ...", LOGIN_URL)
        try:
            await page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            logger.error("无法访问登录页: %s", e)
            await browser.close()
            return 1

        # 等待 Cloudflare 检查完成（如有）
        await wait_for_cloudflare(page)
        await asyncio.sleep(2)

        current_url = page.url
        logger.info("当前页面: %s", current_url)
        logger.info("页面标题: %s", await page.title())

        # ── 4. 判断当前状态 ──
        if await is_logged_in(page):
            logger.info("✓ 检测到已有有效登录会话，直接提取 Cookie")
        elif await is_on_login_page(page):
            logger.info("检测到登录页面，开始填写表单")

            # 填入邮箱密码
            await fill_login_form(page, email, password)

            # 检测验证码
            captcha_type = await detect_captcha(page)
            if captcha_type:
                logger.info("检测到人机验证: %s", captcha_type)
            else:
                logger.info("未检测到显式人机验证元素")

            # ── 5. 用户手动操作提示 ──
            print()
            print("┌" + "─" * 52 + "┐")
            print("│  请在浏览器窗口中完成以下操作：".ljust(51) + "│")
            print("│".ljust(51) + "│")
            print("│  1. 完成人机验证（如有）".ljust(51) + "│")
            print("│  2. 点击登录按钮".ljust(51) + "│")
            print("│".ljust(51) + "│")
            print("│  登录成功后脚本会自动提取 Cookie".ljust(51) + "│")
            print("└" + "─" * 52 + "┘")
            print()

            # 尝试自动点击登录（如果验证码不需要交互）
            await asyncio.sleep(1)
            await try_click_login(page)

            # 等待登录成功
            if await wait_for_login_success(page):
                logger.info("✓ 检测到登录成功")
            else:
                logger.warning("未在 %d 秒内检测到登录，将继续提取当前 Cookie", LOGIN_TIMEOUT)
                input("\n>>> 如果尚未完成登录，请在浏览器中操作后按 Enter 继续...")
        else:
            logger.warning(
                "未知页面状态 (URL: %s)，将尝试提取当前 Cookie",
                current_url,
            )

        # ── 6. 提取 Cookie ──
        await asyncio.sleep(1)
        cookies = await context.cookies()

        if not cookies:
            logger.error("未提取到任何 Cookie，可能登录未成功")
            await browser.close()
            return 1

        cookie_str = cookies_to_str(cookies)
        cookie_dict = cookies_to_dict(cookies)

        logger.info("提取到 %d 个 Cookie 字段", len(cookies))
        for name in cookie_dict:
            value_preview = cookie_dict[name][:40]
            if len(cookie_dict[name]) > 40:
                value_preview += "..."
            logger.info("  %s = %s", name, value_preview)

        # ── 7. 保存到 config.json ──
        config["cookie"] = cookie_str
        save_config(config)

        # ── 8. 验证 Cookie 有效性 ──
        logger.info("-" * 40)
        logger.info("验证 Cookie 有效性...")
        try:
            resp = await page.request.get(USER_URL, max_redirects=0)
            if resp.status == 200:
                text = await resp.text()
                if "login" not in text.lower() or 'class="login' not in text.lower():
                    logger.info("✓ Cookie 有效！当前保持登录状态")
                else:
                    logger.warning("⚠ 页面仍包含登录表单，Cookie 可能无效")
            elif resp.status in (301, 302):
                location = resp.headers.get("location", "")
                if "login" in location:
                    logger.warning("⚠ 被重定向到登录页，Cookie 可能无效")
                else:
                    logger.info("重定向到 %s", location)
            else:
                logger.warning("⚠ 用户页面返回 HTTP %d", resp.status)
        except Exception as e:
            logger.warning("⚠ Cookie 验证请求失败: %s", e)

        # ── 9. 完成 ──
        await asyncio.sleep(2)
        await browser.close()

    print()
    print("=" * 56)
    print("   ✓ Cookie 刷新完成")
    print()
    print("   现在可以运行签到:")
    print("   python checkin.py")
    print("=" * 56)
    print()

    return 0


# ═══════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    sys.exit(asyncio.run(refresh_cookie()))
