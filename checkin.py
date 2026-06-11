"""
ikuuu 自动签到 - Cookie 版
从 config.json 或环境变量读取 Cookie，直接签到
Cookie 过期时需手动更新，不再自动登录
"""
import json
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

# ── 可配置常量 ──────────────────────────────────────────────
BASE_URL = os.environ.get('IKUUU_BASE_URL', 'https://ikuuu.win')
REQUEST_TIMEOUT = int(os.environ.get('IKUUU_TIMEOUT', '15'))
MAX_RETRIES = int(os.environ.get('IKUUU_MAX_RETRIES', '3'))
RETRY_BACKOFF = float(os.environ.get('IKUUU_RETRY_BACKOFF', '2.0'))

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)

# 签到记录文件（防止同一天重复签到）
CHECKIN_LOG = Path(__file__).parent / '.checkin_log'

# ── 日志 ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def parse_cookie(cookie_str: str) -> dict[str, str]:
    """将 Cookie 字符串解析为 dict"""
    cookies: dict[str, str] = {}
    if not cookie_str or not cookie_str.strip():
        return cookies
    for item in cookie_str.split(';'):
        item = item.strip()
        if '=' in item:
            k, v = item.split('=', 1)
            cookies[k.strip()] = v.strip()
    return cookies


def get_cookie() -> str:
    """从环境变量或 config.json 读取 Cookie 字符串"""
    cookie_str = os.environ.get('IKUUU_COOKIE', '')
    if cookie_str:
        return cookie_str

    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        cookie_str = config.get('cookie', '') or config.get('cookie_str', '')
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as e:
        logger.error('config.json JSON 格式错误: %s', e)

    return cookie_str


def build_session(cookie_str: str) -> requests.Session:
    """根据 Cookie 构建带默认 Headers 的 Session"""
    sess = requests.Session()
    sess.headers.update({
        'User-Agent': USER_AGENT,
        'Origin': BASE_URL,
        'Referer': f'{BASE_URL}/user',
        'X-Requested-With': 'XMLHttpRequest',
    })
    cookies = parse_cookie(cookie_str)
    sess.cookies.update(cookies)
    return sess


def request_with_retry(
    method: str,
    url: str,
    session: requests.Session,
    **kwargs: object,
) -> requests.Response:
    """带指数退避重试的 HTTP 请求"""
    timeout: int = kwargs.pop('timeout', REQUEST_TIMEOUT)  # type: ignore[assignment]
    last_exception: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)  # type: ignore[arg-type]
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exception = e
            if attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF ** attempt
                logger.warning(
                    '请求失败 (尝试 %d/%d): %s，%ds 后重试...',
                    attempt, MAX_RETRIES, e, delay,
                )
                time.sleep(delay)
        except requests.RequestException:
            # 非网络错误不重试，直接抛出
            raise

    raise last_exception  # type: ignore[misc]


def validate_cookie(sess: requests.Session) -> tuple[bool, str]:
    """验证 Cookie 是否有效，返回 (有效, 诊断信息)"""
    try:
        r = request_with_retry(
            'GET', f'{BASE_URL}/user', sess, allow_redirects=False,
        )
        location = r.headers.get('Location', '')

        if r.status_code in (302, 301) and 'login' in location:
            return False, f'重定向到登录页 (HTTP {r.status_code})'

        if r.status_code == 200:
            text_lower = r.text.lower()
            # Cloudflare 拦截检测
            if 'cloudflare' in text_lower and 'just a moment' in text_lower:
                return False, '被 Cloudflare 拦截（可能需要更换 IP 或等待）'
            if 'cf-browser-verification' in text_lower:
                return False, '触发 Cloudflare 浏览器验证'
            # 检查页面是否仍是登录表单
            if 'login' in text_lower and '<form' in text_lower:
                return False, '页面仍为登录表单（Cookie 可能已失效）'
            return True, '有效'

        return False, f'HTTP {r.status_code}，非预期状态码'
    except requests.ConnectionError as e:
        return False, f'网络连接失败: {e}'
    except requests.Timeout:
        return False, f'请求超时 ({REQUEST_TIMEOUT}s)'
    except requests.RequestException as e:
        return False, f'请求异常: {e}'


def do_checkin(sess: requests.Session) -> dict:
    """执行签到，返回签到结果 JSON"""
    r = request_with_retry('POST', f'{BASE_URL}/user/checkin', sess)
    try:
        return r.json()  # type: ignore[no-any-return]
    except ValueError:
        logger.error(
            '签到响应非 JSON (HTTP %d): %s', r.status_code, r.text[:200],
        )
        raise


def interpret_result(result: dict) -> tuple[bool, str]:
    """解析签到结果，返回 (成功, 消息)"""
    ret = result.get('ret', -1)
    msg = result.get('msg', '')

    if ret == 1:
        return True, f'签到成功! {msg}'
    if ret == 0:
        if '已经签到' in msg or 'already' in msg.lower():
            return True, '今日已签到，无需重复'
        return False, f'签到失败: {msg}'
    return False, f'未知响应码 ret={ret}: {result}'


def already_checked_in_today() -> bool:
    """检查今天是否已经签到成功过（通过本地记录文件）"""
    if not CHECKIN_LOG.exists():
        return False
    try:
        last_date = CHECKIN_LOG.read_text(encoding='utf-8').strip()
        return last_date == str(date.today())
    except (OSError, UnicodeDecodeError):
        return False


def mark_checked_in() -> None:
    """记录今日签到成功"""
    CHECKIN_LOG.write_text(str(date.today()), encoding='utf-8')


def write_github_summary(lines: list[str]) -> None:
    """如果运行在 GitHub Actions 中，附加内容到 step summary"""
    summary_file = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_file:
        with open(summary_file, 'a', encoding='utf-8') as f:
            for line in lines:
                f.write(line + '\n')


def main() -> None:
    """主签到流程"""
    logger.info('=== ikuuu 自动签到开始 ===')

    # ── 读取 Cookie ──
    cookie_str = get_cookie()
    if not cookie_str:
        logger.error(
            '未提供 Cookie'
            '（设置 IKUUU_COOKIE 环境变量，或在 config.json 中填入 cookie 字段）',
        )
        sys.exit(1)

    # ── 构建 Session ──
    sess = build_session(cookie_str)
    cookies_dict = parse_cookie(cookie_str)
    logger.info('使用 Cookie: email=%s', cookies_dict.get('email', '?'))

    # ── 验证 Cookie ──
    valid, diagnostic = validate_cookie(sess)
    if not valid:
        logger.error('Cookie 验证失败: %s', diagnostic)
        logger.error('请手动更新 Cookie（浏览器登录后从开发者工具复制）')
        sys.exit(1)
    logger.info('Cookie 有效 ✓ (%s)', diagnostic)

    # ── 防重复签到 ──
    if already_checked_in_today():
        logger.info('今日已签到（根据本地记录），跳过签到请求')
        write_github_summary([
            '## ✅ ikuuu 签到',
            '',
            f'- **状态**: 今日已签到（跳过）',
            f'- **时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
        ])
        return

    # ── 执行签到 ──
    logger.info('执行签到...')
    try:
        result = do_checkin(sess)
        success, message = interpret_result(result)

        if success:
            logger.info('✓ %s', message)
            mark_checked_in()
            write_github_summary([
                '## ✅ ikuuu 签到',
                '',
                f'- **状态**: {message}',
                f'- **时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            ])
        else:
            logger.error('✗ %s', message)
            write_github_summary([
                '## ❌ ikuuu 签到失败',
                '',
                f'- **原因**: {message}',
                f'- **时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            ])
            sys.exit(1)
    except Exception as e:
        logger.error('签到异常: %s', e)
        sys.exit(1)
    finally:
        logger.info('=== ikuuu 自动签到结束 ===')


if __name__ == '__main__':
    main()