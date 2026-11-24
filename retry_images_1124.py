import asyncio, sys
from pathlib import Path
sys.path.insert(0, "/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

ROOT = Path("/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
OUT = ROOT / "data/pending/2026-11-24"
JOBS = [
    ("van-makeit", "makeit.jpg", "https://www.todocanada.ca/wp-content/uploads/Make-It-The-Handmade-Revolution24.jpg"),
    ("van-japan-market", "japan-market.png", "https://www.todocanada.ca/wp-content/uploads/Christmas-Poster-Japan.png"),
]

async def main():
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        ctx = await new_stealth_context(browser, settings, city_slug="vancouver")
        page = await ctx.new_page()
        for slug, fname, url in JOBS:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if resp and resp.ok:
                    body = await resp.body()
                    if len(body) > 5000:
                        (OUT / slug / fname).write_bytes(body)
                        print(slug, "SAVED", fname, len(body), flush=True)
                    else:
                        print(slug, "too small", len(body), "status", resp.status, flush=True)
                else:
                    print(slug, "status", resp.status if resp else 0, flush=True)
            except Exception as e:
                print(slug, "ERR", str(e)[:150], flush=True)
            await asyncio.sleep(1)
        await ctx.close()
        await browser.close()

asyncio.run(main())
print("DONE")
