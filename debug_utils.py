"""
Shared browser utilities for debug scripts.
Launches Chromium via Playwright with consistent settings.
"""
import asyncio
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

DEFAULT_VIEWPORT = {"width": 1280, "height": 900}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


async def launch_browser(
    headless: bool = False,
    viewport: Optional[dict] = None,
    user_agent: Optional[str] = None,
) -> tuple[Browser, BrowserContext, Page]:
    """启动 Chromium 浏览器并返回 (browser, context, page)"""
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=headless)
    context = await browser.new_context(
        viewport=viewport or DEFAULT_VIEWPORT,
        user_agent=user_agent or DEFAULT_USER_AGENT,
    )
    page = await context.new_page()
    return browser, context, page


def setup_request_monitor(
    page: Page,
    keyword_filters: Optional[list[str]] = None,
) -> None:
    """在页面上监听网络请求和响应并打印。

    Args:
        page: Playwright Page 对象
        keyword_filters: 如果提供，仅打印 URL 中含任一关键词的响应；
                         如果为 None，打印所有请求和响应。
    """
    page.on("request", lambda req: print(f"[请求] {req.method} {req.url}"))

    if keyword_filters:
        def on_response_filtered(res):
            url_lower = res.url.lower()
            if any(kw in url_lower for kw in keyword_filters):
                print(f"[响应] {res.status} {res.url}")
        page.on("response", on_response_filtered)
    else:
        page.on(
            "response",
            lambda res: print(f"[响应] {res.status} {res.url}"),
        )


async def print_page_info(page: Page) -> None:
    """打印当前页面的基本信息"""
    print(f"\n当前 URL: {page.url}")
    print(f"页面标题: {await page.title()}")
