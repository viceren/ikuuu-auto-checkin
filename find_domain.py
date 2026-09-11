"""
ikuuu 域名探测工具

ikuuu 的域名会不定期更换：旧域名会被替换成一个"最新域名"公告页
（用混淆 JS 动态渲染域名列表，普通 HTTP 请求抓不到）。

本工具用真实浏览器渲染发布页，解析出当前可用的主要/备用域名，
省去手动翻公告页的麻烦。

用法:
    python find_domain.py            # 探测并打印结果
    python find_domain.py --json     # 以 JSON 输出（便于脚本消费）

依赖: playwright (pip install playwright && playwright install chromium)

说明：域名列表来自官方发布页的实时渲染结果，若页面结构变化需相应调整。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("缺少 playwright，请先安装：pip install playwright && playwright install chromium")
    raise SystemExit(1)

# 已知的发布页候选（旧域名通常保留为域名公告页）
PUBLISHER_URLS = [
    "https://ikuuu.win/",
    "https://ikuuu.top/",
    "https://ikuuu.pw/",
    "https://ikuuu.one/",
    "https://ikuuu.me/",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 域名特征
DOMAIN_RE = re.compile(
    r"^https?://(?P<host>[a-z0-9-]+(?:\.[a-z0-9-]+)+)/?$", re.I
)
STATUS_OK = ("在线", "可用", "正常")
STATUS_BAD = ("离线", "不可用", "失效")


async def _probe_page(page, url: str) -> dict | None:
    """渲染一个页面，若为域名公告页则解析出域名列表。"""
    try:
        await page.goto(url, wait_until="networkidle", timeout=45000)
    except Exception:
        return None
    await page.wait_for_timeout(3000)

    title = await page.title()
    body = ""
    try:
        body = await page.inner_text("body")
    except Exception:
        pass

    # 判断是否为"最新域名"公告页
    if "最新域名" not in title and "最新域名" not in body:
        return None

    domains = _parse_body(body)

    # 兜底：正文里没解析到时，退回到 <a> 链接
    if not domains:
        links = await page.eval_on_selector_all(
            "a", "els => els.map(e => e.href)"
        )
        hosts: list[str] = []
        for href in links:
            m = DOMAIN_RE.match(href or "")
            if not m:
                continue
            host = m.group("host").lower()
            if host != "tailwindcss.com" and host not in hosts:
                hosts.append(host)
        domains = [
            {"host": h, "url": f"https://{h}/", "online": None, "role": ""}
            for h in hosts
        ]

    if not domains:
        return None

    return {
        "publisher": url,
        "title": title.strip(),
        "domains": domains,
    }


def _parse_body(body: str) -> list[dict]:
    """从公告页正文里解析「域名 / 状态 / 角色」三元组。

    页面卡片渲染出的文本形如：
        ikuuu.top
        在线
        主要域名
        访问网站
        ikuuu.pw
        离线
        备用域名 1
    """
    lines = [x.strip() for x in body.split("\n") if x.strip()]
    results: list[dict] = []
    seen: set[str] = set()

    for i, line in enumerate(lines):
        m = re.fullmatch(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)", line, re.I)
        if not m:
            continue
        host = m.group(1).lower()
        if host in ("tailwindcss.com",) or host in seen:
            continue

        online: bool | None = None
        role = ""
        # 状态与角色通常紧跟在域名之后
        for j in range(i + 1, min(i + 6, len(lines))):
            nxt = lines[j]
            if nxt in STATUS_OK:
                online = True
            elif nxt in STATUS_BAD:
                online = False
            elif "域名" in nxt:
                role = nxt
            # 遇到下一个域名则停止
            if re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", nxt, re.I):
                break

        seen.add(host)
        results.append({
            "host": host,
            "url": f"https://{host}/",
            "online": online,
            "role": role or "备用",
        })

    return results


async def find_domains() -> list[dict]:
    """依次尝试发布页，返回第一个成功解析的结果列表。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=USER_AGENT, viewport={"width": 1280, "height": 900}
        )
        page = await ctx.new_page()

        found: list[dict] = []
        for url in PUBLISHER_URLS:
            result = await _probe_page(page, url)
            if result and result["domains"]:
                found.append(result)
                break

        await browser.close()
        return found


def _pick_recommended(domains: list[dict]) -> str | None:
    """挑选推荐域名。

    发布页标记的"在线/离线"会实时波动，不完全可信，
    因此优先采用**实测可达**的结果，实测不可达的一律不推荐。
    """
    def reachable(d: dict) -> bool:
        r = d.get("reachable")
        return r is True

    ok = [d for d in domains if reachable(d)]
    if ok:
        for d in ok:
            if "主要" in (d.get("role") or ""):
                return d["url"]
        return ok[0]["url"]

    # 全部实测失败时退回发布页标记（并提示存疑）
    online = [d for d in domains if d.get("online") is True]
    return online[0]["url"] if online else None


def verify_domain(url: str, timeout: int = 20) -> bool:
    """实际请求域名，确认它是正常站点而非"最新域名"公告页。"""
    try:
        import requests
    except ImportError:
        return False
    try:
        resp = requests.get(
            url.rstrip("/") + "/auth/login",
            timeout=timeout,
            allow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
    except Exception:
        return False
    if resp.status_code not in (200, 301, 302):
        return False
    text = resp.content.decode("utf-8", "replace")
    # 返回公告页 = 该域名已失效
    return "最新域名" not in text


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 ikuuu 最新可用域名")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    result = asyncio.run(find_domains())

    if not result:
        if args.json:
            print(json.dumps({"ok": False, "reason": "未解析到域名列表"}, ensure_ascii=False))
        else:
            print("✗ 未能解析到域名列表。")
            print("  可能原因：发布页结构变化，或本站已停止使用该公告页机制。")
            print("  请手动访问旧域名查看公告页。")
        return 1

    data = result[0]
    domains = data["domains"]

    # 实测每个域名的真实可达性（发布页标记仅供参照）
    if not args.json:
        print("\n  正在实测各域名可达性...")
    for d in domains:
        d["reachable"] = verify_domain(d["url"])

    recommended = _pick_recommended(domains)

    if args.json:
        print(json.dumps(
            {"ok": True, "recommended": recommended, **data},
            ensure_ascii=False, indent=2,
        ))
        return 0

    print()
    print("=" * 60)
    print("   ikuuu 域名探测结果")
    print("=" * 60)
    print()
    print(f"  发布页: {data['publisher']}")
    print(f"  页面标题: {data['title']}")
    print()
    print(f"  {'域名':<20} {'角色':<12} {'发布页':<8} {'实测'}")
    print("  " + "-" * 56)
    for d in domains:
        claimed = "在线" if d.get("online") is True else (
            "离线" if d.get("online") is False else "未知"
        )
        real = "可达 ✓" if d.get("reachable") else "不可达 ✗"
        role = d.get("role") or "备用"
        print(f"  {d['host']:<20} {role:<12} {claimed:<8} {real}")
    print()
    if recommended:
        print(f"  推荐使用: {recommended}")
        print()
        print("  应用方式（任选其一）：")
        print(f"    · 本地临时: set IKUUU_BASE_URL={recommended}")
        print("    · 代码默认值: 修改 checkin.py / refresh_cookie.py 的 BASE_URL")
        print(f"    · CI 侧: 仓库 Variables 里设置 IKUUU_BASE_URL={recommended}")
    else:
        print("  ⚠ 没有实测可达的域名，请人工确认（也可能本机网络无法直连）。")
    print("=" * 60)
    print()
    return 0 if recommended else 1


if __name__ == "__main__":
    sys.exit(main())
