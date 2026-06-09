"""
ikuuu 自动签到 - 账号密码版
支持从 config.json 或环境变量读取账号密码
自动登录获取 Cookie 并执行签到
Cookie 过期时自动重新登录
"""
import requests, json, sys, os, asyncio
from datetime import datetime


def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')


def get_config():
    """从 config.json 和环境变量读取配置"""
    config = {}
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        pass

    # 环境变量优先
    config['email'] = os.environ.get('IKUUU_EMAIL', '') or config.get('email', '') or config.get('username', '')
    config['password'] = os.environ.get('IKUUU_PASSWORD', '') or config.get('password', '') or config.get('passwd', '')
    config['cookie'] = os.environ.get('IKUUU_COOKIE', '') or config.get('cookie', '') or config.get('cookie_str', '')

    return config


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
        r = sess.get('https://ikuuu.win/user', timeout=15, allow_redirects=False)
        if r.status_code in (302, 301) or 'login' in r.headers.get('Location', ''):
            return False
        return True
    except Exception:
        return False


def do_checkin(sess):
    """执行签到，返回结果信息"""
    r = sess.post('https://ikuuu.win/user/checkin', timeout=15)
    return r.json()


def main():
    config = get_config()
    sess = requests.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Origin': 'https://ikuuu.win',
        'Referer': 'https://ikuuu.win/user',
        'X-Requested-With': 'XMLHttpRequest',
    })

    cookie_str = config.get('cookie', '')

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
        email = config.get('email', '')
        password = config.get('password', '')

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