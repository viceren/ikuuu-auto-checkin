"""
ikuuu 自动签到 - Cookie 认证版
支持从 config.json 或环境变量 IKUUU_COOKIE 读取 Cookie
"""
import requests, json, sys, os
from datetime import datetime

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')

def main():
    # 读取 Cookie：优先环境变量，其次 config.json
    cookie_str = os.environ.get('IKUUU_COOKIE', '')
    if not cookie_str:
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            cookie_str = config.get('cookie', '')
        except FileNotFoundError:
            log('错误: 找不到 config.json 且环境变量 IKUUU_COOKIE 未设置')
            sys.exit(1)

    if not cookie_str:
        log('错误: Cookie 为空')
        sys.exit(1)

    # 解析 Cookie
    cookies = {}
    for item in cookie_str.split(';'):
        item = item.strip()
        if '=' in item:
            k, v = item.split('=', 1)
            cookies[k] = v

    log(f'Cookie 字段: {list(cookies.keys())}')
    log(f'用户: {cookies.get("email", "未知")}')
    log(f'UID: {cookies.get("uid", "未知")}')

    # 创建 session
    sess = requests.Session()
    sess.cookies.update(cookies)
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Origin': 'https://ikuuu.win',
        'Referer': 'https://ikuuu.win/user',
        'X-Requested-With': 'XMLHttpRequest',
    })

    # 验证 Cookie
    log('验证 Cookie...')
    try:
        r = sess.get('https://ikuuu.win/user', timeout=15, allow_redirects=False)
        if r.status_code in (302, 301) or 'login' in r.headers.get('Location', ''):
            log('Cookie 已过期，请重新获取!')
            sys.exit(1)
        log('Cookie 有效 ✓')
    except Exception as e:
        log(f'验证失败: {e}')
        sys.exit(1)

    # 签到
    log('执行签到...')
    try:
        r = sess.post('https://ikuuu.win/user/checkin', timeout=15)
        result = r.json()
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