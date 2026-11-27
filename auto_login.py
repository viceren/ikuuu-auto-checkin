"""
ikuuu 自动登录探针 — 触发 Geetest v4 并判定能否自动通过

这份代码的定位（请先读）
------------------------
**结论（2026-11-27 实测）：当前 ikuuu 的极验对自动化浏览器一律弹出九宫格语义点选
（题干形如「选 3 个符合右图的图片」），不存在"一键放行"。** 所以本模块**无法**自动登录
拿到 Cookie，也**没有**接入 checkin.py / GitHub Actions 主流程。

它保留下来有两个用途：

1. **环境探针**：``python auto_login.py --dry-run`` —— 不碰任何真实账号，一条命令
   得出"极验此刻对自动化浏览器是放行还是出题"。站点策略随时会变，日后想复测直接跑。
2. **接打码平台（方案 B）的底座**：打开登录页 → 填表 → 触发极验 → 等待结果，这段
   流程是通用的；真要做自动登录时，只需把"等待放行"换成"调打码平台拿验证参数再提交"。

2026-11-27 的 7 组对照实验（均在本机住宅 IP 下，结果一律为出九宫格）
---------------------------------------------------------------------
    1. Playwright 默认无头（headless shell）+ 伪装 UA
    2. 新版无头 channel="chromium"
    3. 系统真实 Edge 159
    4. 系统真实 Chrome 158
    5. 无头 + UA 与 Client Hints 版本自洽（去掉 HeadlessChrome 字样）
    6. **有头** + 真实 Chrome + 真实 Intel GPU
    7. 第 6 组基础上 + 人类鼠标轨迹 + 逐字输入
连"有头 + 真实 Chrome + 真实 GPU + 真人般操作"都直接出题，说明拦截点不是浏览器指纹，
而是 ikuuu 把极验配置成了「必须作答」。因此 xvfb + 有头、stealth 加固等方向**无需再试**。

凭据来源（密码只走环境变量，绝不落盘、绝不入日志）
--------------------------------------------------
    IKUUU_EMAIL / IKUUU_PASSWORD      环境变量（本地调试用）
    config.json 的 email / password   本地兜底（不推荐把密码写进去）

用法
----
    python auto_login.py --dry-run    # 不提交登录、不需要真实账号：极验放行 or 出题？
    python auto_login.py              # 真实登录（预期会在极验处停下并报出题干）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import captcha_solver

logger = logging.getLogger("auto_login")

BASE_URL = os.environ.get("IKUUU_BASE_URL") or "https://ikuuu.top"
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# 启动参数：去掉最显眼的自动化特征（无害，保留给未来底座用）
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]
STEALTH_INIT_JS = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)
# 浏览器偏好：优先系统安装的真实 Chrome / Edge；"chromium" = Playwright 自带新版无头。
# 可用 IKUUU_BROWSER_CHANNEL=chrome|msedge|chromium 强制指定。
DEFAULT_CHANNELS = ("chrome", "msedge", "chromium")

# ── 页面结构（2026-11-27 对 https://ikuuu.top/auth/login 实测）──────────────
# 登录页为 JS 混淆动态渲染，requests 抓不到表单，选择器只能靠真实浏览器确认。
EMAIL_SEL = "#email"
PASSWORD_SEL = "#password, input[name='passwd'], input[type='password']"
LOGIN_BTN_SEL = "button.login"
# 「点我开始验证」按钮是 .geetest_btn_click（不是 .geetest_radar_btn，后者在 ikuuu 不存在）
GEETEST_BTN_SEL = ".geetest_btn_click"
GEETEST_READY_JS = (
    "() => !!(window.Captcha && typeof window.Captcha.isReady === 'function' "
    "&& window.Captcha.isReady())"
)
# 极验状态。注意：.geetest_tip 只是状态条（出题时仍显示"验证中"），**不能**拿它判断；
# 真正的题干在 .geetest_text_tips / .geetest_title 里。
GEETEST_STATE_JS = """() => {
    const vis = (s) => { const e = document.querySelector(s); if (!e) return false;
        const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const txt = (s) => { const e = document.querySelector(s);
        return e ? (e.innerText || '').trim() : ''; };
    const C = window.Captcha || {};
    return {
        ready: typeof C.isReady === 'function' ? !!C.isReady() : false,
        error: typeof C.getError === 'function' ? (C.getError() || null) : null,
        window: vis('.geetest_window'),
        question: txt('.geetest_text_tips') || txt('.geetest_title')
                  || txt('.geetest_ques_tips'),
        nine: vis('.geetest_nine') || document.querySelectorAll('.geetest_item').length > 0,
        slide: vis('.geetest_slice_bg') || vis('.geetest_arrow') || vis('.geetest_slider'),
    };
}"""
# SSPanel 会话 Cookie 的核心字段，缺任何一个都视为登录不完整
REQUIRED_COOKIE_FIELDS = ("uid", "key", "expire_in")

GEETEST_TIMEOUT = float(os.environ.get("IKUUU_GEETEST_TIMEOUT", "30"))
LOGIN_ATTEMPTS = int(os.environ.get("IKUUU_LOGIN_ATTEMPTS", "6"))
PAGE_TIMEOUT_MS = int(os.environ.get("IKUUU_PAGE_TIMEOUT_MS", "60000"))

DRY_RUN_PASSED = "DRY_RUN_PASSED"


class LoginFailed(Exception):
    """登录失败。``retryable=False`` 表示再试也没用（出题、要邮箱验证码等），立即停止。"""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


# ═══════════════════════════════════════════════════════════
#  凭据 / Cookie 落盘
# ═══════════════════════════════════════════════════════════


def load_credentials() -> tuple[str, str]:
    """读取登录凭据：环境变量优先，其次 config.json。返回 (email, password)，缺失为空串。"""
    email = (os.environ.get("IKUUU_EMAIL") or "").strip()
    password = (os.environ.get("IKUUU_PASSWORD") or "").strip()
    if email and password:
        return email, password

    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("读取 config.json 失败: %s", e)
        else:
            email = email or (config.get("email") or "").strip()
            password = password or (config.get("password") or "").strip()
    return email, password


def save_cookie_to_config(cookie_str: str) -> bool:
    """把 Cookie 写入本地 config.json（保留其余字段）。文件不存在则不创建，返回 False。"""
    if not CONFIG_PATH.exists():
        return False
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = {}
    config["cookie"] = cookie_str
    try:
        CONFIG_PATH.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        logger.warning("写入 config.json 失败: %s", e)
        return False
    logger.info("✓ 新 Cookie 已写入 %s", CONFIG_PATH)
    return True


# ═══════════════════════════════════════════════════════════
#  浏览器 / 拟人操作
# ═══════════════════════════════════════════════════════════


def _channels() -> list[str | None]:
    forced = (os.environ.get("IKUUU_BROWSER_CHANNEL") or "").strip().lower()
    if forced in DEFAULT_CHANNELS:
        return [forced]
    return list(DEFAULT_CHANNELS)


def _sane_user_agent(browser_version: str, channel: str | None) -> str:
    """构造与浏览器真实版本自洽的 UA。

    无头模式下 Chrome 默认 UA 末尾是 ``HeadlessChrome/xxx``，直接暴露；
    这里换成 ``Chrome/<真实主版本>``，与 navigator.userAgentData.brands 保持一致。
    """
    major = (browser_version or "0").split(".")[0]
    suffix = " Edg/%s.0.0.0" % major if channel == "msedge" else ""
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36{suffix}"
    )


async def _launch_browser(playwright, headless: bool):
    """按偏好依次尝试启动浏览器。返回 (browser, 实际使用的 channel)。"""
    errors: list[str] = []
    for channel in _channels():
        kwargs = {"headless": headless, "args": LAUNCH_ARGS}
        if channel:
            kwargs["channel"] = channel
        try:
            browser = await playwright.chromium.launch(**kwargs)
        except Exception as e:  # noqa: BLE001 - 换下一个候选
            errors.append(f"{channel or 'default'}: {str(e).splitlines()[0][:80]}")
            continue
        logger.debug("浏览器启动成功: channel=%s version=%s", channel, browser.version)
        return browser, channel
    raise RuntimeError(
        "无法启动浏览器（已尝试 chrome / msedge / chromium）：" + " | ".join(errors)
    )


async def _bezier_move(page, tx: float, ty: float, click: bool = False) -> None:
    """沿二次贝塞尔曲线把鼠标移到目标，带抖动与变速；可选按下/抬起。"""
    sx, sy = random.uniform(300, 900), random.uniform(250, 650)
    await page.mouse.move(sx, sy)
    await asyncio.sleep(random.uniform(0.15, 0.35))
    cx = (sx + tx) / 2 + random.uniform(-160, 160)
    cy = (sy + ty) / 2 + random.uniform(-110, 110)
    steps = random.randint(30, 45)
    for i in range(1, steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * tx + random.uniform(-1.3, 1.3)
        y = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * ty + random.uniform(-1.3, 1.3)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.005, 0.024))
    await asyncio.sleep(random.uniform(0.12, 0.3))
    if click:
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.06, 0.14))
        await page.mouse.up()


async def _human_click(page, selector: str) -> None:
    box = await page.locator(selector).first.bounding_box()
    if not box:
        await page.locator(selector).first.click(timeout=3000)
        return
    await _bezier_move(
        page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, click=True
    )


async def _type_like_human(page, selector: str, text: str) -> None:
    await _human_click(page, selector)
    await asyncio.sleep(random.uniform(0.2, 0.4))
    await page.keyboard.type(text, delay=random.randint(80, 130))


# ═══════════════════════════════════════════════════════════
#  极验
# ═══════════════════════════════════════════════════════════


async def _wait_cloudflare(page, max_seconds: int = 20) -> None:
    """等待 Cloudflare「正在检查浏览器」中间页结束（如出现）。"""
    for _ in range(max_seconds):
        cf = await page.query_selector(
            "#cf-please-wait, #challenge-form, #ChallengeBody"
        )
        if not cf:
            return
        await asyncio.sleep(1)


async def _pass_geetest(page, provider, timeout: float = GEETEST_TIMEOUT) -> str:
    """点击「点我开始验证」；出题则交给 captcha_solver 解题，直到放行。

    Returns:
        通过的说明文字。

    Raises:
        LoginFailed: 解题失败（retryable=False）或等待超时（retryable=True）。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    clicked = 0
    last_error = None

    while loop.time() < deadline:
        try:
            state = await page.evaluate(GEETEST_STATE_JS)

            if state["ready"]:
                return f"极验已放行（点击 {clicked} 次）"

            # 出题（九宫格 / 文字点选 / 滑块）→ 交解题器
            if state["window"] and (
                state["question"] or state["nine"] or state["slide"]
            ):
                logger.info("检测到极验出题：%r，交给解题器 %s",
                            state["question"], provider)
                ok, msg = await captcha_solver.solve_challenge(page, provider)
                if ok:
                    return msg
                if msg == "TEXT_CHALLENGE":
                    raise LoginFailed(
                        "遇到文字点选题型（CLIP 暂不支持），换会话重试", retryable=True
                    )
                raise LoginFailed(f"极验解题失败：{msg}", retryable=False)

            last_error = state["error"]

            button = page.locator(GEETEST_BTN_SEL)
            if await button.count() and await button.first.is_visible():
                try:
                    await _human_click(page, GEETEST_BTN_SEL)
                    clicked += 1
                except Exception:  # noqa: BLE001 - 被遮罩挡住等瞬时情况，下一轮再试
                    pass
        except LoginFailed:
            raise
        except Exception:  # noqa: BLE001 - 页面导航/重绘期间的瞬时异常
            pass

        # 极验评估需要时间，点击后留足间隔再轮询
        await asyncio.sleep(2)

    raise LoginFailed(
        f"等待极验放行超时（共点击 {clicked} 次"
        f"{f'，最后错误: {last_error}' if last_error else ''}）"
    )


# ═══════════════════════════════════════════════════════════
#  登录
# ═══════════════════════════════════════════════════════════


def _cookies_to_str(cookies: list[dict], base_url: str) -> str:
    """把浏览器上下文里属于目标站点的 Cookie 拼成 ``k=v; k2=v2``。"""
    host = (urlparse(base_url).hostname or "").lower()
    parts: list[str] = []
    for c in cookies:
        domain = (c.get("domain") or "").lstrip(".").lower()
        if host and domain and not (
            host == domain or host.endswith("." + domain) or domain.endswith("." + host)
        ):
            continue
        parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


async def _explain_login_failure(page) -> str:
    hint = ""
    for sel in (".swal2-html-container", ".swal2-content", ".swal2-title"):
        try:
            loc = page.locator(sel)
            if await loc.count():
                text = (await loc.first.inner_text()).strip()
                if text:
                    hint = text
                    break
        except Exception:  # noqa: BLE001
            pass

    body = ""
    try:
        body = await page.inner_text("body")
    except Exception:  # noqa: BLE001
        pass

    # 登录页本身不含"验证码"三字（实测），出现即说明站点要求邮件二次验证
    if "验证码" in body and ("邮箱" in body or "邮件" in body):
        return (
            "站点要求邮箱验证码（新设备/异地登录触发），本工具无法自动完成，"
            "请本地运行 python refresh_cookie.py 手动刷新"
        )
    return f"登录未进入用户中心（url={page.url}）{('：' + hint[:120]) if hint else ''}"


async def _login_once(
    email: str,
    password: str,
    base_url: str,
    dry_run: bool = False,
    headless: bool = True,
    provider=None,
) -> str:
    """单次尝试：全新浏览器 + 上下文。成功返回 Cookie 字符串；dry-run 放行返回 DRY_RUN_PASSED。

    Raises:
        LoginFailed: 未能通过极验 / 登录被拒。
        RuntimeError: 环境问题（缺 playwright / 起不了浏览器）。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise RuntimeError(
            "缺少 playwright 或其浏览器内核，请执行: "
            "pip install playwright && playwright install chromium"
        ) from e

    login_url = f"{base_url}/auth/login"
    async with async_playwright() as p:
        browser, channel = await _launch_browser(p, headless=headless)
        try:
            context = await browser.new_context(
                locale="zh-CN",
                viewport={"width": 1280, "height": 800},
                user_agent=_sane_user_agent(browser.version, channel),
            )
            await context.add_init_script(STEALTH_INIT_JS)

            page = await context.new_page()
            await page.goto(
                login_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS
            )
            await _wait_cloudflare(page)
            await page.wait_for_selector(EMAIL_SEL, timeout=20000)
            await asyncio.sleep(random.uniform(0.8, 1.5))

            await _type_like_human(page, EMAIL_SEL, email)
            await asyncio.sleep(random.uniform(0.3, 0.6))
            await _type_like_human(page, PASSWORD_SEL, password)
            await asyncio.sleep(random.uniform(0.6, 1.1))

            passed = await _pass_geetest(
                page, provider or captcha_solver.create_provider()
            )
            if dry_run:
                return f"{DRY_RUN_PASSED}: {passed}"

            await _human_click(page, LOGIN_BTN_SEL)
            try:
                await page.wait_for_url("**/user**", timeout=20000)
            except Exception:  # noqa: BLE001
                await asyncio.sleep(2)

            if "/user" not in page.url:
                raise LoginFailed(await _explain_login_failure(page), retryable=False)

            cookie_str = _cookies_to_str(await context.cookies(), base_url)
            lowered = cookie_str.lower()
            if not all(f"{name}=" in lowered for name in REQUIRED_COOKIE_FIELDS):
                raise LoginFailed(
                    f"登录后 Cookie 缺少会话字段（{cookie_str[:80]}...）", retryable=False
                )
            return cookie_str
        finally:
            await browser.close()


def run_login(
    email: str | None = None,
    password: str | None = None,
    base_url: str | None = None,
    attempts: int = LOGIN_ATTEMPTS,
    dry_run: bool = False,
    headless: bool = True,
) -> tuple[str | None, str]:
    """同步入口。返回 ``(cookie, 说明)``；dry-run 放行时 cookie 为 DRY_RUN_PASSED。

    只有"等待超时 / 浏览器异常"才重试；出题、要邮箱验证码这类**再试也没用**的
    情况会立即停止，避免浪费时间。
    """
    base_url = (base_url or BASE_URL).rstrip("/")
    if not dry_run:
        if not email or not password:
            email, password = load_credentials()
        if not email or not password:
            return None, (
                "未配置邮箱或密码（设置 IKUUU_EMAIL / IKUUU_PASSWORD），无法登录"
            )

    last_message = "未执行"
    provider = captcha_solver.create_provider()
    logger.info("极验解题器: %s", provider)
    for attempt in range(1, attempts + 1):
        try:
            result = asyncio.run(
                _login_once(email or "", password or "", base_url,
                            dry_run=dry_run, headless=headless, provider=provider)
            )
        except LoginFailed as e:
            last_message = e.message
            logger.warning("第 %d/%d 次: %s", attempt, attempts, e.message)
            if not e.retryable:
                break
        except RuntimeError as e:
            return None, str(e)  # 环境问题，重试无意义
        except Exception as e:  # noqa: BLE001 - 任何异常都不该让调用方崩溃
            last_message = f"浏览器异常: {e}"
            logger.warning("第 %d/%d 次: %s", attempt, attempts, last_message)
        else:
            if dry_run:
                return DRY_RUN_PASSED, result
            logger.info("✓ 登录成功（第 %d 次尝试）", attempt)
            return result, "登录成功"

        if attempt < attempts:
            time.sleep(3)

    return None, last_message


# ═══════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════


def _preview(cookie_str: str, keep: int = 60) -> str:
    """Cookie 预览：只露前 keep 个字符，避免终端/日志里完整泄露。"""
    return cookie_str if len(cookie_str) <= keep else cookie_str[:keep] + "..."


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ikuuu 自动登录（Playwright + 极验 v4 解题）"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="不提交登录、不需要真实账号：验证极验能否被自动解出")
    parser.add_argument("--headed", action="store_true",
                        help="以有头模式运行（会弹出浏览器窗口）")
    parser.add_argument("--save", action="store_true",
                        help="登录成功后把 Cookie 写入本地 config.json")
    parser.add_argument("--attempts", type=int, default=LOGIN_ATTEMPTS,
                        help="最多尝试次数（仅对超时/异常生效）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.dry_run:
        result, message = run_login(
            email="dry-run@example.com", password="dry-run",
            attempts=args.attempts, dry_run=True, headless=not args.headed,
        )
        print()
        if result == DRY_RUN_PASSED:
            print(f"✓ {message}")
            print("  极验已被自动解出。真实登录还取决于账号密码、"
                  "以及站点是否要求邮箱验证码。")
            raise SystemExit(0)
        print(f"✗ {message}")
        print("  解题未通过。常见原因：VLM 未配置或模型未加载（IKUUU_VLM_PROVIDER）、"
              "识别错误、或遇到尚未支持的题型。")
        print("  可先设 IKUUU_VLM_PROVIDER=manual 用人工兜底验证链路。")
        raise SystemExit(1)

    result, message = run_login(
        attempts=args.attempts, headless=not args.headed
    )
    print()
    if not result:
        print(f"✗ 登录失败: {message}")
        raise SystemExit(1)

    print(f"✓ {message}，Cookie 长度 {len(result)}")
    print(f"  预览: {_preview(result)}")
    if args.save:
        save_cookie_to_config(result)
    else:
        print("  （未落盘；加 --save 可写入 config.json）")
