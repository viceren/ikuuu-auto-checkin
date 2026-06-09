"""
ikuuu 自动登录 - Playwright 浏览器自动化
从账号密码登录，提取可用的 Cookie 字符串
"""
import asyncio, json, os, sys

BASE_URL = 'https://ikuuu.win'
LOGIN_URL = f'{BASE_URL}/auth/login'


def get_credentials():
    """
    从环境变量或 config.json 读取账号密码和 Cookie。
    返回 (email, password, cookie_str)，缺失的字段为空字符串。
    """
    email = os.environ.get('IKUUU_EMAIL', '')
    password = os.environ.get('IKUUU_PASSWORD', '')
    cookie_str = os.environ.get('IKUUU_COOKIE', '')

    if email and password:
        return email, password, cookie_str

    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        email = email or config.get('email', '') or config.get('username', '')
        password = password or config.get('password', '') or config.get('passwd', '')
        cookie_str = cookie_str or config.get('cookie', '') or config.get('cookie_str', '')
    except FileNotFoundError:
        pass

    return email, password, cookie_str


async def login_and_get_cookie(
    email: str = None,
    password: str = None,
    headless: bool = True,
    timeout: int = 30000,
) -> str:
    """
    使用 Playwright 登录 ikuuu，返回完整 Cookie 字符串。

    参数:
        email: 账号，不传则从 config.json / 环境变量读取
        password: 密码，不传则从 config.json / 环境变量读取
        headless: 是否无头模式（CI 必须 True）
        timeout: 超时（毫秒）

    返回:
        Cookie 字符串，格式同 document.cookie
    """
    # 获取凭据
    if not email or not password:
        e, p, _ = get_credentials()
        email = email or e
        password = password or p

    if not email or not password:
        print('[login] 错误: 未提供账号密码（config.json 或 IKUUU_EMAIL/IKUUU_PASSWORD 环境变量）')
        sys.exit(1)

    print(f'[login] 准备登录: {email}')

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
            ] if headless else [],
        )
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36',
        )
        page = await context.new_page()

        try:
            # 访问登录页
            print(f'[login] 访问登录页: {LOGIN_URL}')
            await page.goto(LOGIN_URL, timeout=timeout, wait_until='networkidle')

            # 等待页面加载
            await page.wait_for_timeout(2000)

            # 尝试多种选择器找到邮箱/密码输入框
            email_selectors = [
                'input[name="email"]',
                'input[type="email"]',
                'input[placeholder*="邮箱"]',
                'input[placeholder*="mail"]',
                'input#email',
                'input[name="login"]',
            ]
            password_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                'input[placeholder*="密码"]',
                'input[placeholder*="pass"]',
                'input#password',
                'input[name="passwd"]',
            ]

            email_input = None
            for sel in email_selectors:
                email_input = await page.query_selector(sel)
                if email_input:
                    print(f'[login] 找到邮箱输入框: {sel}')
                    break

            pwd_input = None
            for sel in password_selectors:
                pwd_input = await page.query_selector(sel)
                if pwd_input:
                    print(f'[login] 找到密码输入框: {sel}')
                    break

            if not email_input or not pwd_input:
                html = await page.content()
                print(f'[login] 未找到输入框，页面HTML(前2000字符):\n{html[:2000]}')
                raise Exception('无法定位登录输入框')

            # 填入账号密码
            await email_input.fill(email)
            await pwd_input.fill(password)
            print('[login] 已填入账号密码')

            # 查找登录按钮并点击
            submit_selectors = [
                'button[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Login")',
                'input[type="submit"]',
                '.btn-primary',
                'button.btn',
                '.login-btn',
                'button:has-text("登 录")',
                'form button',
                'form input[type="submit"]',
            ]

            submit_btn = None
            for sel in submit_selectors:
                submit_btn = await page.query_selector(sel)
                if submit_btn:
                    print(f'[login] 找到登录按钮: {sel}')
                    break

            if not submit_btn:
                print('[login] 未找到登录按钮，尝试回车提交')
                await page.keyboard.press('Enter')
            else:
                await submit_btn.click()

            # 等待登录完成：URL 跳转离开登录页，或超时
            try:
                await page.wait_for_url(
                    lambda url: '/auth/login' not in url,
                    timeout=15000,
                )
                print(f'[login] 登录后 URL: {page.url}')
            except Exception:
                # 超时未跳转，说明可能登录失败或有验证码
                current_url = page.url
                print(f'[login] 等待跳转超时，当前 URL: {current_url}')
                if '/auth/login' in current_url:
                    body_text = await page.inner_text('body')
                    print(f'[login] 页面内容(前500字):\n{body_text[:500]}')
                    raise Exception('登录失败，页面未跳转（可能是验证码或账号密码错误）')

            # 提取 Cookie
            cookies = await context.cookies()
            cookie_str = '; '.join(f'{c["name"]}={c["value"]}' for c in cookies)
            print(f'[login] 登录成功! Cookie 字段: {[c["name"] for c in cookies]}')

            # 验证 Cookie 有效性
            print('[login] 验证 Cookie...')
            user_page = await context.new_page()
            await user_page.goto(f'{BASE_URL}/user', timeout=15000, wait_until='domcontentloaded')
            user_url = user_page.url
            if '/auth/login' in user_url:
                raise Exception('登录后 Cookie 无效，仍被重定向到登录页')
            print(f'[login] Cookie 有效 ✓ 当前页面: {user_url}')

            return cookie_str

        finally:
            await browser.close()


def main():
    """CLI 入口：登录并输出 Cookie 字符串"""
    cookie = asyncio.run(login_and_get_cookie(headless='--headless' in sys.argv))
    print(f'\n=== Cookie ===\n{cookie}\n==============')


if __name__ == '__main__':
    main()