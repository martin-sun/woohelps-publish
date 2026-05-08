"""Spike 2: 深入调查 TodoCanada 实际页面结构"""

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

        # ========== 1: 探索正确的城市 URL ==========
        print("=" * 60)
        print("1: 探索正确的城市 URL pattern")
        print("=" * 60)

        test_urls = [
            "https://www.todocanada.ca/city/toronto/",
            "https://www.todocanada.ca/city/toronto/events/",
            "https://www.todocanada.ca/city/vancouver/",
            "https://www.todocanada.ca/city/vancouver/events/",
            "https://www.todocanada.ca/city/calgary/",
            "https://www.todocanada.ca/toronto/",
        ]

        for url in test_urls:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)
            status = resp.status if resp else "no response"
            final_url = page.url
            title = await page.title()
            print(f"\n  {url}")
            print(f"    status={status}")
            print(f"    final_url={final_url[:100]}")
            print(f"    title={title[:80]}")

        # ========== 2: 分析城市 events 页面的链接结构 ==========
        print("\n" + "=" * 60)
        print("2: 分析 /city/toronto/ 页面的链接结构")
        print("=" * 60)

        await page.goto("https://www.todocanada.ca/city/toronto/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        all_links = await page.eval_on_selector_all(
            "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 100) || ''}))"
        )

        # 找出不同的 URL pattern
        patterns = {}
        for link in all_links:
            href = link["href"]
            if "todocanada.ca" not in href:
                continue
            path = href.split("todocanada.ca")[1].split("?")[0].split("#")[0]
            if not path or path == "/":
                continue
            # 归类
            if re.match(r"^/city/[^/]+/event/", path):
                key = "city_event"
            elif re.match(r"^/city/[^/]+/events/?$", path):
                key = "city_events"
            elif re.match(r"^/city/[^/]+/category/", path):
                key = "city_category"
            elif re.match(r"^/city/[^/]+/post_tag/", path):
                key = "city_tag"
            elif re.match(r"^/city/[^/]+/$", path):
                key = "city_root"
            elif re.match(r"^/[a-z-]+-[a-z]+-[^/]+/$", path) and "/city/" not in path:
                key = "article_slug"
            elif re.match(r"^/city/[^/]+/", path):
                key = "city_other"
            else:
                key = "other"
            patterns.setdefault(key, []).append({"href": href, "text": link["text"], "path": path})

        for cat, links in patterns.items():
            print(f"\n  [{cat}] ({len(links)} 个):")
            for l in links[:6]:
                print(f"    {l['path'][:80]}")
                if l['text']:
                    print(f"      text: {l['text'][:60]}")
            if len(links) > 6:
                print(f"    ... 还有 {len(links) - 6} 个")

        # ========== 3: 查看 /city/toronto/events/ 页面 ==========
        print("\n" + "=" * 60)
        print("3: /city/toronto/events/ 页面内容")
        print("=" * 60)

        resp = await page.goto("https://www.todocanada.ca/city/toronto/events/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        print(f"  status={resp.status if resp else 'no response'}")
        print(f"  final_url={page.url[:100]}")
        print(f"  title={await page.title()}")

        # 找所有链接
        links = await page.eval_on_selector_all(
            "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 100) || ''}))"
        )

        # 过滤出文章链接
        article_links = []
        for l in links:
            href = l["href"]
            if "todocanada.ca" not in href:
                continue
            path = href.split("todocanada.ca")[1].split("?")[0].split("#")[0]
            # 跳过导航链接
            if path.startswith("/city/") or path.startswith("/Submission") or path == "/":
                continue
            if len(path) > 5 and not path.startswith("/Things") and not path.startswith("/ideas"):
                article_links.append(l)

        print(f"\n  文章类链接 ({len(article_links)} 个):")
        for l in article_links[:15]:
            print(f"    {l['href'][:100]}")
            if l['text']:
                print(f"      text: {l['text'][:80]}")

        # ========== 4: 查看 article 页面是否包含活动信息 ==========
        print("\n" + "=" * 60)
        print("4: 文章页面结构分析")
        print("=" * 60)

        # 取一个看起来像活动列表的文章
        for l in article_links:
            text_lower = (l.get("text") or "").lower()
            href_lower = l["href"].lower()
            if any(kw in text_lower for kw in ["may", "june", "weekend", "event", "things to do"]) or \
               any(kw in href_lower for kw in ["may", "june", "weekend", "event", "things"]):
                test_url = l["href"]
                print(f"  测试文章: {test_url[:100]}")
                print(f"  text: {l.get('text', '')[:80]}")

                await page.goto(test_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3)

                html = await page.content()
                og_image = await page.eval_on_selector(
                    'meta[property="og:image"]', 'el => el?.getAttribute("content")'
                )
                print(f"  HTML 大小: {len(html)} 字符")
                print(f"  og:image: {og_image}")

                # 检查是否有日期/时间/地点等关键词
                date_mentions = re.findall(r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}', html[:5000])
                time_mentions = re.findall(r'\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)', html[:5000])
                print(f"  日期关键词（前5000字符）: {date_mentions[:5]}")
                print(f"  时间关键词（前5000字符）: {time_mentions[:5]}")
                break

        await browser.close()
        print("\n✅ TodoCanada Spike 2 完成")


if __name__ == "__main__":
    asyncio.run(spike())
