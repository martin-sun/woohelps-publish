"""Spike: TodoCanada 城市覆盖 + DiscoverSaskatoon + FamilyFunCanada"""

import asyncio
import re
from playwright.async_api import async_playwright


async def spike_todocanada_cities(page):
    print("=" * 60)
    print("TodoCanada: 所有城市 events 页状态")
    print("=" * 60)

    cities = ["toronto", "vancouver", "calgary", "edmonton", "ottawa",
              "winnipeg", "montreal", "saskatoon", "regina", "moncton"]
    for city in cities:
        url = f"https://www.todocanada.ca/city/{city}/events/"
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        status = resp.status if resp else "no response"
        title = await page.title()
        # 统计详情页链接
        event_count = await page.eval_on_selector_all(
            "a",
            f'els => els.filter(a => a.href.match(/todocanada\\.ca\\/city\\/{city}\\/event\\//)).length'
        )
        print(f"  {city}: status={status}, events={event_count}, title={title[:50]}")


async def spike_discoversaskatoon(page):
    print("\n" + "=" * 60)
    print("DiscoverSaskatoon: 页面结构验证")
    print("=" * 60)

    url = "https://www.discoversaskatoon.com/calendar-events"
    print(f"  访问: {url}")
    resp = await page.goto(url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(2)
    status = resp.status if resp else "no response"
    print(f"  status={status}")
    print(f"  title={await page.title()}")

    # 收集所有链接
    all_links = await page.eval_on_selector_all(
        "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 80) || ''}))"
    )
    print(f"  总链接数: {len(all_links)}")

    # 过滤详情页链接
    detail_links = []
    seen = set()
    for l in all_links:
        href = l["href"]
        if "discoversaskatoon.com" in href and href not in seen:
            path = href.split("discoversaskatoon.com")[1].split("?")[0].split("#")[0]
            if path and path != "/calendar-events" and path != "/":
                seen.add(href)
                detail_links.append({"href": href, "text": l["text"], "path": path})

    print(f"\n  内部链接 ({len(detail_links)} 个):")
    for l in detail_links[:20]:
        print(f"    {l['path'][:80]}  text: {l['text'][:40]}")

    # Load More 按钮
    print("\n  检查 Load More:")
    btn = await page.query_selector('button:has-text("Load")')
    if btn:
        text = await btn.text_content()
        print(f"  找到按钮: {text}")
        # 点击一次看效果
        await btn.click()
        await page.wait_for_timeout(2000)
        new_links = await page.eval_on_selector_all("a", "els => els.length")
        print(f"  点击后链接数: {new_links}")
    else:
        print("  无 Load More 按钮")

    # 分页
    pagination = await page.query_selector(".pagination, .pager, nav.pagination")
    if pagination:
        print(f"  找到分页元素")

    # 详情页测试
    event_links = [l for l in detail_links if re.search(r'/calendar-events/', l['path']) and l['path'] != '/calendar-events']
    if event_links:
        test_url = event_links[0]["href"]
        print(f"\n  测试详情页: {test_url}")
        await page.goto(test_url, wait_until="networkidle", timeout=20000)
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
                for key in ['name', 'startDate', 'endDate', 'location', 'description']:
                    if key in data:
                        print(f"    {key}: {str(data[key])[:100]}")
            except:
                pass
    else:
        print("\n  未找到事件详情页链接")


async def spike_familyfun(page):
    print("\n" + "=" * 60)
    print("FamilyFunCanada: 页面结构验证")
    print("=" * 60)

    city = "toronto"
    url = f"https://www.familyfuncanada.com/{city}/"
    print(f"  访问: {url}")
    resp = await page.goto(url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(2)
    status = resp.status if resp else "no response"
    print(f"  status={status}")
    print(f"  title={await page.title()}")

    # 收集文章链接
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

    # 分页
    print("\n  检查分页:")
    for page_num in [2, 3]:
        next_url = f"https://www.familyfuncanada.com/{city}/page/{page_num}/"
        resp = await page.goto(next_url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)
        status = resp.status if resp else "no response"
        title = await page.title()
        links = await page.eval_on_selector_all(
            "a", 'els => els.filter(a => a.href.match(/familyfuncanada\\.com\\/\\d{4}\\/\\d{2}\\//)).length'
        )
        print(f"  page/{page_num}/: status={status}, articles={links}, title={title[:50]}")

    # 详情页测试
    if article_links:
        test_url = article_links[0]["href"]
        print(f"\n  测试文章页: {test_url}")
        await page.goto(test_url, wait_until="networkidle", timeout=20000)
        await asyncio.sleep(2)

        html = await page.content()
        title = await page.title()
        og_image = await page.eval_on_selector(
            'meta[property="og:image"]', 'el => el?.getAttribute("content")'
        )
        print(f"  title: {title[:80]}")
        print(f"  og:image: {og_image}")
        print(f"  HTML 大小: {len(html)} 字符")

    # 其他城市
    print("\n  其他城市测试:")
    for c in ["vancouver", "calgary", "edmonton", "saskatoon"]:
        url = f"https://www.familyfuncanada.com/{c}/"
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)
        status = resp.status if resp else "no response"
        title = await page.title()
        articles = await page.eval_on_selector_all(
            "a", 'els => els.filter(a => a.href.match(/familyfuncanada\\.com\\/\\d{4}\\/\\d{2}\\//)).length'
        )
        print(f"    {c}: status={status}, articles={articles}, title={title[:50]}")


async def main():
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

        await spike_todocanada_cities(page)
        await spike_discoversaskatoon(page)
        await spike_familyfun(page)

        await browser.close()
        print("\n✅ 所有 Spike 验证完成")


if __name__ == "__main__":
    asyncio.run(main())
