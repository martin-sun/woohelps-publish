"""Spike: FamilyFunCanada 验证"""

import asyncio
import re
from playwright.async_api import async_playwright


async def spike():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        city = "toronto"
        url = f"https://www.familyfuncanada.com/{city}/"
        print(f"访问: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        print(f"  title: {await page.title()}")

        # 1. 收集文章链接
        all_links = await page.eval_on_selector_all(
            "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 100) || ''}))"
        )

        article_links = []
        seen = set()
        for l in all_links:
            href = l["href"]
            if re.search(r"familyfuncanada\.com/\d{4}/\d{2}/", href) and href not in seen:
                seen.add(href)
                article_links.append(l)

        print(f"\n  文章链接 ({len(article_links)} 个):")
        for l in article_links[:10]:
            print(f"    {l['href'][:100]}")
            if l['text']:
                print(f"      text: {l['text'][:60]}")

        # 2. 分页
        print("\n  === 分页测试 ===")
        for page_num in [2, 3]:
            next_url = f"https://www.familyfuncanada.com/{city}/page/{page_num}/"
            resp = await page.goto(next_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            status = resp.status if resp else "no response"
            title = await page.title()
            count = await page.eval_on_selector_all(
                "a", 'els => els.filter(a => a.href.match(/familyfuncanada\\.com\\/\\d{4}\\/\\d{2}\\//)).length'
            )
            print(f"  page/{page_num}/: status={status}, articles={count}, title={title[:50]}")

        # 3. 详情页结构
        print("\n  === 文章详情页结构 ===")
        if article_links:
            test_url = article_links[0]["href"]
            print(f"  测试: {test_url}")
            await page.goto(test_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            html = await page.content()
            title = await page.title()
            og_image = await page.eval_on_selector(
                'meta[property="og:image"]', 'el => el?.getAttribute("content")'
            )
            print(f"  title: {title[:80]}")
            print(f"  og:image: {og_image}")
            print(f"  HTML 大小: {len(html)} 字符")

            # JSON-LD
            json_ld = await page.eval_on_selector_all(
                'script[type="application/ld+json"]', 'els => els.map(el => el.textContent)'
            )
            import json
            for i, ld in enumerate(json_ld):
                try:
                    data = json.loads(ld)
                    t = data.get('@type', 'unknown')
                    print(f"  JSON-LD #{i+1}: type={t}")
                    if isinstance(t, list):
                        t = t[0]
                    for key in ['name', 'startDate', 'endDate', 'location', 'description', 'datePublished']:
                        if key in data:
                            print(f"    {key}: {str(data[key])[:100]}")
                except:
                    pass

        # 4. 多城市测试
        print("\n  === 多城市测试 ===")
        for c in ["vancouver", "calgary", "edmonton", "saskatoon", "montreal", "ottawa", "winnipeg"]:
            city_url = f"https://www.familyfuncanada.com/{c}/"
            resp = await page.goto(city_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            status = resp.status if resp else "no response"
            title = await page.title()
            count = await page.eval_on_selector_all(
                "a", 'els => els.filter(a => a.href.match(/familyfuncanada\\.com\\/\\d{4}\\/\\d{2}\\//)).length'
            )
            print(f"    {c}: status={status}, articles={count}, title={title[:50]}")

        await browser.close()
        print("\n✅ FamilyFunCanada Spike 完成")


if __name__ == "__main__":
    asyncio.run(spike())
