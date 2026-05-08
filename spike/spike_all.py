"""Spike: TodoCanada 详情页 + 分页验证"""

import asyncio
import re
from playwright.async_api import async_playwright


async def spike():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # ========== 1: 收集 events 页的详情页链接 ==========
        print("=" * 60)
        print("1: /city/toronto/events/ 详情页链接收集")
        print("=" * 60)

        await page.goto("https://www.todocanada.ca/city/toronto/events/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(4)

        all_links = await page.eval_on_selector_all(
            "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 100) || ''}))"
        )

        detail_links = []
        seen = set()
        for l in all_links:
            href = l["href"]
            if re.search(r"todocanada\.ca/city/[^/]+/event/[^/]+/.*/?$", href):
                if href not in seen:
                    seen.add(href)
                    detail_links.append(l)

        print(f"  详情页链接数: {len(detail_links)}")
        for l in detail_links[:10]:
            path = l["href"].split("todocanada.ca")[1]
            print(f"    {path}")
            print(f"      text: {l['text'][:60]}")

        # ========== 2: 分页 ==========
        print("\n" + "=" * 60)
        print("2: 分页行为")
        print("=" * 60)

        # 检查分页链接
        page_links = await page.eval_on_selector_all(
            "a", 'els => els.filter(a => a.href.includes("page")).map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 50)}))'
        )
        print(f"  包含 'page' 的链接 ({len(page_links)} 个):")
        for l in page_links[:10]:
            print(f"    {l['href'][:100]}  text: {l['text']}")

        # 检查是否有 Load More / pagination
        btn = await page.query_selector('button:has-text("Load")')
        if btn:
            print("  找到 Load More 按钮")
        else:
            print("  无 Load More 按钮")

        # 检查 page/2/
        await page.goto("https://www.todocanada.ca/city/toronto/events/page/2/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        page2_title = await page.title()
        print(f"\n  page/2/ status: 200, url={page.url[:80]}, title={page2_title[:60]}")

        # ========== 3: 详情页内容分析 ==========
        print("\n" + "=" * 60)
        print("3: 详情页内容分析")
        print("=" * 60)

        if detail_links:
            for test_url in [detail_links[0]["href"]]:
                print(f"\n  访问: {test_url.split('todocanada.ca')[1]}")
                await page.goto(test_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3)

                html = await page.content()
                title = await page.title()
                og_image = await page.eval_on_selector(
                    'meta[property="og:image"]', 'el => el?.getAttribute("content")'
                )

                print(f"  title: {title[:80]}")
                print(f"  og:image: {og_image}")
                print(f"  HTML 大小: {len(html)} 字符")

                # 提取 JSON-LD
                json_ld = await page.eval_on_selector_all(
                    'script[type="application/ld+json"]',
                    'els => els.map(el => el.textContent)'
                )
                if json_ld:
                    import json
                    for i, ld in enumerate(json_ld):
                        try:
                            data = json.loads(ld)
                            print(f"\n  JSON-LD #{i+1}: type={data.get('@type', 'unknown')}")
                            if '@type' in data:
                                for key in ['name', 'startDate', 'endDate', 'location', 'description', 'image']:
                                    if key in data:
                                        val = data[key]
                                        if isinstance(val, dict):
                                            print(f"    {key}: {json.dumps(val, ensure_ascii=False)[:100]}")
                                        else:
                                            print(f"    {key}: {str(val)[:100]}")
                        except:
                            pass

        # ========== 4: 反爬测试 ==========
        print("\n" + "=" * 60)
        print("4: 快速连续访问测试（5次）")
        print("=" * 60)

        if detail_links and len(detail_links) >= 5:
            for i in range(5):
                url = detail_links[i]["href"]
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                status = resp.status if resp else "no response"
                print(f"  请求 {i+1}: status={status}")

        # ========== 5: 所有城市 events 页 ==========
        print("\n" + "=" * 60)
        print("5: 所有城市 events 页状态")
        print("=" * 60)

        cities = ["toronto", "vancouver", "calgary", "edmonton", "ottawa",
                   "winnipeg", "montreal", "saskatoon", "regina", "moncton"]
        for city in cities:
            url = f"https://www.todocanada.ca/city/{city}/events/"
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            status = resp.status if resp else "no response"
            title = await page.title()
            event_count = len(await page.eval_on_selector_all(
                "a",
                f'els => els.filter(a => a.href.match(/todocanada\\.ca\\/city\\/{city}\\/event\\//)).length'
            ))
            print(f"  {city}: status={status}, events={event_count}, title={title[:50]}")

        await browser.close()
        print("\n✅ TodoCanada Spike 3 完成")


if __name__ == "__main__":
    asyncio.run(spike())
