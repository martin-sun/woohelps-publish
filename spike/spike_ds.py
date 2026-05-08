"""Spike: DiscoverSaskatoon 深入验证"""

import asyncio
import re
from playwright.async_api import async_playwright


async def spike():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = "https://www.discoversaskatoon.com/calendar-events"
        print(f"访问: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)
        print(f"  title: {await page.title()}")

        # 1. 列出所有 discoversaskatoon.com 链接
        all_links = await page.eval_on_selector_all(
            "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 100) || ''}))"
        )

        ds_links = {}
        for l in all_links:
            href = l["href"]
            if "discoversaskatoon.com" not in href:
                continue
            path = href.split("discoversaskatoon.com")[1].split("?")[0].split("#")[0]
            if not path or path == "/":
                continue
            # 归类
            if path.startswith("/calendar-events/submit"):
                key = "submit"
            elif path.startswith("/calendar-events"):
                key = "calendar_events"
            elif path.startswith("/places-to-stay"):
                key = "stays"
            elif path.startswith("/explore"):
                key = "explore"
            else:
                key = "other"
            ds_links.setdefault(key, []).append({"path": path, "text": l["text"]})

        for cat, links in ds_links.items():
            print(f"\n  [{cat}] ({len(links)} 个):")
            for l in links[:10]:
                print(f"    {l['path'][:80]}  text: {l['text'][:50]}")
            if len(links) > 10:
                print(f"    ... 还有 {len(links) - 10} 个")

        # 2. 仔细查看 calendar_events 下的链接
        cal_links = ds_links.get("calendar_events", [])
        print(f"\n  === calendar-events 相关链接详细 ===")
        for l in cal_links:
            print(f"    {l['path']}")
            print(f"      text: {l['text'][:80]}")

        # 3. 检查事件卡片 HTML 结构
        print("\n  === 检查页面中的事件卡片 ===")

        # 看看是否有事件列表
        cards = await page.query_selector_all(".event-card, .event-item, .views-row, .node--type-event, article")
        print(f"  事件卡片选择器匹配: {len(cards)} 个")

        # 查看 views-row (Drupal 常见)
        views_rows = await page.query_selector_all(".views-row")
        print(f"  .views-row: {len(views_rows)} 个")
        if views_rows:
            for i, row in enumerate(views_rows[:3]):
                html = await row.inner_html()
                text = await row.inner_text()
                print(f"\n  .views-row #{i+1}:")
                print(f"    text: {text[:200]}")
                # 获取链接
                links = await row.eval_on_selector_all("a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()}))")
                for l in links[:3]:
                    print(f"    link: {l['href'][:80]}  text: {l['text'][:50]}")

        # 4. 分页结构
        print("\n  === 分页结构 ===")
        pager = await page.query_selector_all(".pager, .pagination, nav")
        for p_el in pager[:3]:
            text = await p_el.inner_text()
            print(f"    pager text: {text[:100]}")
            links = await p_el.eval_on_selector_all("a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()}))")
            for l in links[:5]:
                print(f"      {l['href'][:80]}  text: {l['text']}")

        # 5. 查看分页是否有 Load More / page 参数
        print("\n  === URL 和分页参数 ===")
        # 检查是否有 page= 参数的链接
        page_param_links = [l for l in all_links if "page=" in l["href"]]
        print(f"  含 page= 的链接: {len(page_param_links)}")
        for l in page_param_links[:5]:
            print(f"    {l['href'][:100]}")

        # 6. 访问一个事件详情页
        print("\n  === 事件详情页测试 ===")
        # 找到可能是事件详情的链接
        event_detail_candidates = [l for l in cal_links if l["path"] != "/calendar-events" and "submit" not in l["path"]]
        if not event_detail_candidates:
            # 尝试从 views-row 的链接中找
            print("  未从 calendar-events 路径找到详情链接，尝试其他方式...")
            # 看看是否有事件标题文字的链接
            for l in all_links:
                href = l["href"]
                text = l["text"]
                if "discoversaskatoon.com" in href and any(kw in text.lower() for kw in ["festival", "concert", "market", "show"]):
                    print(f"    候选: {href[:80]}  text: {text[:50]}")

        await browser.close()
        print("\n✅ DiscoverSaskatoon Spike 完成")


if __name__ == "__main__":
    asyncio.run(spike())
