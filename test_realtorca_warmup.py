#!/usr/bin/env python3
"""测试增强页面预热是否能绕过 Incapsula WAF"""
import asyncio, json
from loguru import logger
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

API_URL = "https://api2.realtor.ca/Listing.svc/AsyncPropertySearch_Post"

async def fetch():
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings, city_slug="saskatoon")

        # 增强预热：完整加载页面，等 JS challenge 完成
        page = await context.new_page()
        logger.info("预热步骤 1: 访问 realtor.ca 主页...")
        await page.goto("https://www.realtor.ca", wait_until="networkidle", timeout=60_000)
        await asyncio.sleep(3)  # 给 Incapsula JS challenge 足够时间

        # 检查 cookies
        cookies = await context.cookies()
        logger.info(f"当前 cookies 数量: {len(cookies)}")
        for c in cookies:
            if 'incap' in c.get('name', '').lower() or 'visid' in c.get('name', '').lower():
                logger.info(f"  WAF cookie: {c['name']}={c['value'][:20]}...")

        logger.info("预热步骤 2: 访问 map 页面...")
        try:
            await page.goto("https://www.realtor.ca/map", wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)
        except Exception:
            logger.warning("Map 页面加载超时，继续用已有 cookies 调 API")

        form_data = {
            "CurrentPage": "1",
            "RecordsPerPage": "11",
            "Sort": "6-D",
            "IndividualId": "2061436",
            "IncludePins": "1",
            "Currency": "CAD",
            "IncludeHiddenListings": "false",
            "ApplicationId": "1",
            "CultureId": "1",
            "Version": "7.0",
        }

        logger.info("调用 API...")
        resp = await context.request.post(API_URL, form=form_data, headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.realtor.ca/map",
            "Origin": "https://www.realtor.ca",
        })

        logger.info(f"API 响应状态: {resp.status}")
        text = await resp.text()
        if resp.status == 200:
            data = json.loads(text)
            paging = data['Paging']
            logger.info(f"✅ 成功! 分页: 第{paging['CurrentPage']}页, 共{paging['TotalPages']}页, 总计{paging['TotalRecords']}条")
            return True
        else:
            logger.error(f"❌ 仍然 403")
            logger.info(f"响应前 300 字: {text[:300]}")
            return False

if __name__ == "__main__":
    ok = asyncio.run(fetch())
    exit(0 if ok else 1)
