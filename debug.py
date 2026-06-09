"""
ikuuu 网站调试分析脚本 - 分析页面结构和登录流程
"""
import asyncio
from playwright.async_api import async_playwright

async def debug():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            executable_path="C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1217/chrome-win64/chrome.exe"
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # 监听所有网络请求
        page.on("request", lambda request: print(f"[请求] {request.method} {request.url}"))
        page.on("response", lambda response: print(f"[响应] {response.status} {response.url}"))

        print("="*60)
        print("Step 1: 访问 https://ikuuu.one/user")
        print("="*60)
        try:
            await page.goto("https://ikuuu.one/user", timeout=30000)
        except Exception as e:
            print(f"导航异常: {e}")

        await asyncio.sleep(5)

        # 获取当前页面信息
        print(f"\n当前URL: {page.url}")
        print(f"页面标题: {await page.title()}")

        # 获取页面所有可见文字
        body_text = await page.inner_text("body")
        print(f"\n页面文字预览(前2000字):\n{body_text[:2000]}")

        # 获取所有按钮
        buttons = await page.query_selector_all("button, a.btn, [type='submit']")
        print(f"\n共找到 {len(buttons)} 个按钮/链接:")
        for i, btn in enumerate(buttons):
            text = await btn.inner_text()
            html = await btn.get_attribute("outerHTML")
            print(f"  按钮{i}: text='{text[:50]}' html='{html[:200]}'")

        # 获取所有 input
        inputs = await page.query_selector_all("input")
        print(f"\n共找到 {len(inputs)} 个输入框:")
        for i, inp in enumerate(inputs):
            name = await inp.get_attribute("name")
            id_ = await inp.get_attribute("id")
            type_ = await inp.get_attribute("type")
            placeholder = await inp.get_attribute("placeholder")
            print(f"  input{i}: name='{name}' id='{id_}' type='{type_}' placeholder='{placeholder}'")

        # 获取表单
        forms = await page.query_selector_all("form")
        print(f"\n共找到 {len(forms)} 个表单:")
        for i, form in enumerate(forms):
            action = await form.get_attribute("action")
            method = await form.get_attribute("method")
            print(f"  form{i}: action='{action}' method='{method}'")

        # 检查是否有 Cloudflare 检测
        cf = await page.query_selector("#cf-please-wait, .cf-browser-verification, #ChallengeBody")
        print(f"\nCloudflare检测: {'发现' if cf else '未发现'}")

        input("\n\n按 Enter 继续下一步...")

        # 如果有登录表单，尝试分析登录流程
        print("\n" + "="*60)
        print("Step 2: 分析登录流程")
        print("="*60)

        # 等待用户手动输入，或者看是否有登录页面重定向
        print("正在检查是否需要先登录...")
        await asyncio.sleep(2)

        # 检查页面上的所有链接
        links = await page.query_selector_all("a")
        print(f"\n共找到 {len(links)} 个链接:")
        for i, link in enumerate(links[:30]):  # 限制30个
            href = await link.get_attribute("href")
            text = await link.inner_text()
            if href and href not in ["#", "", "javascript:;"]:
                print(f"  链接{i}: text='{text[:40]}' href='{href[:80]}'")

        # 查看页面完整 HTML 结构（截取关键部分）
        html = await page.content()
        print(f"\n页面HTML长度: {len(html)} 字符")

        input("\n\n按 Enter 关闭浏览器...")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug())