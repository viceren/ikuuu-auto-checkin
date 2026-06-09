"""
ikuuu 自动签到 - 账号密码版
支持从 config.json 或环境变量读取账号密码
自动登录获取 Cookie 并执行签到
Cookie 过期时自动重新登录
"""
import requests, sys, os, asyncio
from datetime import datetime

BASE_URL = 'https://ikuuu.win'


def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')


def parse_cookie(cookie_str):
    """将 Cookie 字符串解析为 dict"""
    cookies = {}
    for item in cookie_str.split(';'):
        item = item.strip()
        if '=' in item:
            k, v = item.split('=', 1)
            cookies[k] = v
    return cookies


def validate_cookie(sess):
    """验证 Cookie 是否有效，返回 True/False"""
    try:
        r = sess.get(f'{BASE_URL}/user', timeout=15, allow_redirects=False)
        location = r.headers.get('Location', '')
        if r.status_code in (302, 301) and 'login' in location:
            return False
        if r.status_code == 200:
            return True
        # 其他情况（如 403）视为无效
        return False
    except Exception:
        return False


def do_checkin(sess):
    """执行签到，返回结果信息"""
    r = sess.post(f'{BASE_URL}/user/checkin', timeout=15)
    return r.json()


def main():
    sess = requests.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Origin': BASE_URL,
        'Referer': f'{BASE_URL}/user',
        'X-Requested-With': 'XMLHttpRequest',
    })

    # 读取凭据：环境变量优先，其次 config.json
    email = os.environ.get('IKUUU_EMAIL', '')
    password = os.environ.get('IKUUU_PASSWORD', '')
    cookie_str = os.environ.get('IKUUU_COOKIE', '')

    if not email or not password or not cookie_str:
        try:
            from login import get_credentials
            c_email, c_password, c_cookie = get_credentials()
            email = email or c_email
            password = password or c_password
            cookie_str = cookie_str or c_cookie
        except ImportError:
            pass

    # 尝试已有 Cookie
    if cookie_str:
        cookies = parse_cookie(cookie_str)
        sess.cookies.update(cookies)
        log(f'尝试已有 Cookie: email={cookies.get("email", "?")}')

        if validate_cookie(sess):
            log('Cookie 有效 ✓')
        else:
            log('Cookie 已过期，将重新登录...')
            cookie_str = ''  # 清空，触发登录流程
            sess.cookies.clear()

    # Cookie 无效或不存在，自动登录
    if not cookie_str:
        if not email or not password:
            log('错误: 未提供账号密码（config.json 中设置 email/password，'
                '或设置 IKUUU_EMAIL / IKUUU_PASSWORD 环境变量）')
            sys.exit(1)

        log(f'自动登录: {email}')
        try:
            from login import login_and_get_cookie
            new_cookie = asyncio.run(login_and_get_cookie(
                email=email,
                password=password,
                headless=True,
            ))
        except ImportError:
            log('错误: 缺少 login.py，请确保 login.py 在同一目录')
            sys.exit(1)
        except Exception as e:
            log(f'自动登录失败: {e}')
            sys.exit(1)

        cookies = parse_cookie(new_cookie)
        sess.cookies.update(cookies)
        log(f'登录成功! email={cookies.get("email", "?")}')

    # 执行签到
    log('执行签到...')
    try:
        result = do_checkin(sess)
        ret = result.get('ret', -1)
        msg = result.get('msg', '')

        if ret == 1:
            log(f'✓ 签到成功! {msg}')
        elif ret == 0:
            if '已经签到' in msg or 'already' in msg.lower():
                log('今日已签到，无需重复')
            else:
                log(f'签到失败: {msg}')
        else:
            log(f'未知响应: {result}')
    except Exception as e:
        log(f'签到异常: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()