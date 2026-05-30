#!/usr/bin/env python3
import asyncio, re, json
from loguru import logger
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

DETAIL_URL = "https://www.realtor.ca/real-estate/29758682/105-615-stensrud-road-saskatoon-willowgrove"

async def fetch():
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings, city_slug="saskatoon")
        page = await context.new_page()
        
        # 监听网络请求
        api_responses = []
        def on_response(response):
            url = response.url
            if 'api2.realtor.ca' in url or 'realtor.ca' in url:
                if any(x in url.lower() for x in ['listing', 'property', 'detail']):
                    api_responses.append(url)
        page.on("response", on_response)
        
        logger.info(f"访问详情页...")
        await page.goto(DETAIL_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_load_state("networkidle", timeout=30_000)
        await asyncio.sleep(5)
        
        # 尝试从 DOM 提取描述
        desc_selectors = [
            '[class*="description"]',
            '[class*="remark"]',
            '[class*="detail"] p',
            'section:has-text("Description")',
            'div:has-text("Description")',
        ]
        
        for sel in desc_selectors:
            count = await page.locator(sel).count()
            if count > 0:
                texts = await page.locator(sel).all_inner_texts()
                logger.info(f"选择器 '{sel}': 找到 {count} 个元素")
                for t in texts[:3]:
                    t_clean = t.strip()[:200]
                    if len(t_clean) > 50:
                        logger.info(f"  文本: {t_clean}...")
        
        # 也尝试用 inner_text 搜索关键词
        body_text = await page.locator("body").inner_text(timeout=10_000)
        # 查找可能包含描述的段落
        for line in body_text.split('\n'):
            line = line.strip()
            if len(line) > 200 and 'welcome' in line.lower():
                logger.info(f"找到描述段落: {line[:300]}...")
                break
        
        logger.info(f"\nAPI 请求: {api_responses[:10]}")
        
        await context.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(fetch())
