"""Spike: 验证 TodoCanada — 处理 Cloudflare 防护"""

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

        # ========== 测试 1: 列表页 URL pattern ==========
        print("=" * 60)
        print("测试 1: 列表页 URL pattern")
        print("=" * 60)

        city = "toronto"
        list_url = f"https://www.todocanada.ca/{city}/events/"
        print(f"  访问: {list_url}")
        resp = await page.goto(list_url, timeout=30000)
        # 等待 Cloudflare challenge 完成
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        status = resp.status if resp else "no response"
        final_url = page.url
        title = await page.title()
        print(f"  status={status}")
        print(f"  final_url={final_url[:100]}")
        print(f"  title={title[:80]}")

        # 检查是否通过了 Cloudflare
        if "Just a moment" in title or "cf-chl" in final_url:
            print("  ⚠️ Cloudflare challenge 未通过，等待更长时间...")
            await asyncio.sleep(10)
            title = await page.title()
            final_url = page.url
            print(f"  等待后 title={title[:80]}")
            print(f"  等待后 url={final_url[:100]}")

        # ========== 测试 2: 收集详情页链接 ==========
        print("\n" + "=" * 60)
        print("测试 2: 列表页链接分析")
        print("=" * 60)

        all_links = await page.eval_on_selector_all(
            "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 80) || ''}))"
        )

        todocanada_links = [l for l in all_links if "todocanada.ca" in l["href"]]
        print(f"  总链接数: {len(all_links)}, todocanada 链接: {len(todocanada_links)}")

        # 按路径 pattern 归类
        event_detail = []
        events_list = []
        other = []
        for link in todocanada_links:
            href = link["href"]
            if re.search(r"todocanada\.ca/[^/]+/event\b", href):
                event_detail.append(link)
            elif "/events" in href:
                events_list.append(link)
            else:
                other.append(link)

        print(f"\n  详情页链接 (event_detail, {len(event_detail)} 个):")
        for l in event_detail[:8]:
            print(f"    {l['href'][:100]}")
            print(f"      text: {l['text'][:60]}")

        print(f"\n  列表页链接 (events_list, {len(events_list)} 个):")
        for l in events_list[:5]:
            print(f"    {l['href'][:100]}")

        print(f"\n  其他链接 (other, {len(other)} 个):")
        for l in other[:10]:
            print(f"    {l['href'][:100]}")

        # ========== 测试 3: 分页 ==========
        print("\n" + "=" * 60)
        print("测试 3: 分页行为")
        print("=" * 60)

        pagination_selectors = [
            "nav.pagination", ".pagination", "ul.page-numbers",
            ".nav-links", "a.next", "a.next.page-numbers",
            'a:has-text("Next")', 'a:has-text("Older")',
            ".load-more", '#load-more',
        ]
        for sel in pagination_selectors:
            el = await page.query_selector(sel)
            if el:
                text = (await el.text_content() or "").strip()[:60]
                href = await el.get_attribute("href")
                print(f"  找到: {sel} -> text={text}, href={href}")

        # ========== 测试 4: 详情页 ==========
        print("\n" + "=" * 60)
        print("测试 4: 详情页结构")
        print("=" * 60)

        if event_detail:
            test_url = event_detail[0]["href"]
            print(f"  访问详情页: {test_url[:100]}")
            await page.goto(test_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            og_image = await page.eval_on_selector(
                'meta[property="og:image"]', 'el => el?.getAttribute("content")'
            )
            html_len = len(await page.content())
            title = await page.title()
            print(f"  title: {title[:80]}")
            print(f"  og:image: {og_image}")
            print(f"  HTML 大小: {html_len} 字符")
        else:
            print("  未找到详情页链接")

        # ========== 测试 5: 其他城市 ==========
        print("\n" + "=" * 60)
        print("测试 5: 其他城市列表页")
        print("=" * 60)

        for city_slug in ["vancouver", "calgary"]:
            url = f"https://www.todocanada.ca/{city_slug}/events/"
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)
            title = await page.title()
            links_count = len(await page.query_selector_all("a"))
            print(f"  {city_slug}: title={title[:60]}, links={links_count}")

        await browser.close()
        print("\n✅ TodoCanada Spike 完成")


if __name__ == "__main__":
    asyncio.run(spike())
