"""Spike: FamilyFunCanada 详情页测试"""

import asyncio
import re
from playwright.async_api import async_playwright


async def spike():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # 1. 测试几个详情页
        test_urls = [
            "https://www.familyfuncanada.com/toronto/weekend-guide/",
            "https://www.familyfuncanada.com/toronto/victoria-day-weekend/",
            "https://www.familyfuncanada.com/toronto/mothers-day/",
        ]

        for url in test_urls:
            print(f"\n{'='*60}")
            print(f"访问: {url}")
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

            status = resp.status if resp else "no response"
            title = await page.title()
            html = await page.content()
            og_image = await page.eval_on_selector(
                'meta[property="og:image"]', 'el => el?.getAttribute("content")'
            )

            print(f"  status={status}, title={title[:80]}")
            print(f"  og:image: {og_image}")
            print(f"  HTML 大小: {len(html)} 字符")

            # 检查内容区域
            content_el = await page.query_selector(".entry-content, .post-content, article .content, .article-content")
            if content_el:
                text = (await content_el.inner_text())[:300]
                print(f"  内容区: {text[:200]}")

            # JSON-LD
            json_ld = await page.eval_on_selector_all(
                'script[type="application/ld+json"]', 'els => els.map(el => el.textContent)'
            )
            import json
            for ld in json_ld[:2]:
                try:
                    data = json.loads(ld)
                    t = data.get('@type', 'unknown')
                    print(f"  JSON-LD type={t}")
                except:
                    pass

        # 2. 检查 calendar 页面
        print(f"\n{'='*60}")
        print("Calendar 页面")
        cal_url = "https://www.familyfuncanada.com/toronto/calendar/"
        resp = await page.goto(cal_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        status = resp.status if resp else "no response"
        title = await page.title()
        print(f"  status={status}, title={title[:80]}")

        # 收集日历页链接
        cal_links = await page.eval_on_selector_all(
            "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 80) || ''}))"
        )
        ff_cal_links = [l for l in cal_links if "familyfuncanada.com" in l["href"] and l["href"] != cal_url]
        print(f"  链接数: {len(ff_cal_links)}")
        for l in ff_cal_links[:10]:
            path = l["href"].split("familyfuncanada.com")[1]
            print(f"    {path[:80]}  text: {l['text'][:40]}")

        # 3. 收集正确的文章链接 pattern
        print(f"\n{'='*60}")
        print("城市主页文章链接 pattern 分析")
        await page.goto("https://www.familyfuncanada.com/toronto/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        all_links = await page.eval_on_selector_all(
            "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 100) || ''}))"
        )

        # 过滤出看起来是文章的链接
        article_candidates = []
        seen = set()
        for l in all_links:
            href = l["href"]
            if "familyfuncanada.com" not in href or href in seen:
                continue
            path = href.split("familyfuncanada.com")[1].split("?")[0].split("#")[0]
            # 跳过导航、分类等
            if not path or path in ("/", "/toronto/", "/about-us/"):
                continue
            if path.startswith("/toronto/category/") or path.startswith("/toronto/tag/"):
                continue
            if path == "/toronto/calendar/":
                continue
            if path.startswith("/toronto/") and len(path) > len("/toronto/x"):
                seen.add(href)
                article_candidates.append(l)

        print(f"  文章候选链接 ({len(article_candidates)} 个):")
        for l in article_candidates[:15]:
            path = l["href"].split("familyfuncanada.com")[1]
            print(f"    {path[:80]}  text: {l['text'][:60]}")

        await browser.close()
        print("\n✅ FamilyFunCanada 详情页测试完成")


if __name__ == "__main__":
    asyncio.run(spike())
