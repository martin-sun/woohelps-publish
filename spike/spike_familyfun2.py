"""Spike: FamilyFunCanada 深入调查链接结构"""

import asyncio
import re
from playwright.async_api import async_playwright


async def spike():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = "https://www.familyfuncanada.com/toronto/"
        print(f"访问: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        print(f"  title: {await page.title()}")

        # 收集所有链接
        all_links = await page.eval_on_selector_all(
            "a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 100) || ''}))"
        )

        # 分析所有 familyfuncanada 链接
        ff_links = {}
        for l in all_links:
            href = l["href"]
            if "familyfuncanada.com" not in href:
                continue
            path = href.split("familyfuncanada.com")[1].split("?")[0].split("#")[0]
            if not path or path == "/":
                continue

            # 按路径结构分类
            if re.match(r"^/\d{4}/\d{2}/", path):
                key = "yyyy_mm_article"
            elif re.match(r"^/[a-z-]+/$", path):
                key = "city_page"
            elif re.match(r"^/[a-z-]+/page/\d+/$", path):
                key = "city_pagination"
            elif path.startswith("/category/"):
                key = "category"
            elif path.startswith("/tag/"):
                key = "tag"
            elif re.match(r"^/[a-z-]+/[a-z0-9-]+/$", path):
                key = "city_article"
            else:
                key = "other"

            ff_links.setdefault(key, []).append({"path": path, "text": l["text"]})

        for cat, links in sorted(ff_links.items()):
            print(f"\n  [{cat}] ({len(links)} 个):")
            for l in links[:8]:
                print(f"    {l['path'][:80]}  text: {l['text'][:50]}")
            if len(links) > 8:
                print(f"    ... 还有 {len(links) - 8} 个")

        # 特别看看文章内容区的 HTML 结构
        print("\n  === 页面主要内容区域 ===")
        main_selectors = ["main", "article", ".entry-content", ".post", "#content", ".site-content"]
        for sel in main_selectors:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text())[:300]
                print(f"  {sel}: {text[:200]}...")

        # 检查是否有 article 元素
        articles = await page.query_selector_all("article")
        print(f"\n  <article> 元素: {len(articles)} 个")
        for i, art in enumerate(articles[:5]):
            links = await art.eval_on_selector_all("a", "els => els.map(a => ({href: a.href, text: a.textContent?.trim()?.substring(0, 60)}))")
            for l in links[:3]:
                print(f"    article #{i+1}: {l['href'][:80]}  text: {l['text'][:40]}")

        await browser.close()
        print("\n✅ FamilyFunCanada 深入调查完成")


if __name__ == "__main__":
    asyncio.run(spike())
