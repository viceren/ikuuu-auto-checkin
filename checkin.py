"""
ikuuu 自动签到 - Cookie 版
从 config.json 或环境变量读取 Cookie，直接签到
Cookie 过期时需手动更新，不再自动登录
"""
import requests, sys, os
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


def get_cookie():
    """从环境变量或 config.json 读取 Cookie 字符串"""
    cookie_str = os.environ.get('IKUUU_COOKIE', '')
    if cookie_str:
        return cookie_str

    try:
        import json
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        cookie_str = config.get('cookie', '') or config.get('cookie_str', '')
    except FileNotFoundError:
        pass

    return cookie_str


def validate_cookie(sess):
    """验证 Cookie 是否有效，返回 True/False"""
    try:
        r = sess.get(f'{BASE_URL}/user', timeout=15, allow_redirects=False)
        location = r.headers.get('Location', '')
        if r.status_code in (302, 301) and 'login' in location:
            return False
        if r.status_code == 200:
            return True
        return False
    except Exception:
        return False


def do_checkin(sess):
    """执行签到，返回结果信息"""
    r = sess.post(f'{BASE_URL}/user/checkin', timeout=15)
    return r.json()


def main():
    # 读取 Cookie
    cookie_str = get_cookie()
    if not cookie_str:
        log('错误: 未提供 Cookie（设置 IKUUU_COOKIE 环境变量，或在 config.json 中填入 cookie 字段）')
        sys.exit(1)

    sess = requests.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Origin': BASE_URL,
        'Referer': f'{BASE_URL}/user',
        'X-Requested-With': 'XMLHttpRequest',
    })

    cookies = parse_cookie(cookie_str)
    sess.cookies.update(cookies)
    log(f'使用 Cookie: email={cookies.get("email", "?")}')

    # 验证 Cookie
    if not validate_cookie(sess):
        log('Cookie 已失效，请手动更新 Cookie（浏览器登录后从开发者工具复制）')
        sys.exit(1)

    log('Cookie 有效 ✓')

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
