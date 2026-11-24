import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, "/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

async def main():
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        ctx = await new_stealth_context(browser, settings, city_slug="london")
        page = await ctx.new_page()
        url = "https://www.facebook.com/search/events/?q=London%20holiday%20market"
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(6000)
        text = await page.evaluate("() => document.body.innerText")
        print("london-holiday-market: status=", resp.status if resp else 0, "textlen=", len(text))
        print("HEAD:", text[:200].replace("\n", " | "))
        await ctx.close()
        await browser.close()

asyncio.run(main())
print("DONE")
