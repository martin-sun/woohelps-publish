import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, "/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

ROOT = Path("/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
OUT = ROOT / "data/sweep/2026-11-24-activities/fb"
OUT.mkdir(parents=True, exist_ok=True)

QUERIES = {
    "vancouver-christmas": "https://www.facebook.com/search/events/?q=Vancouver%20Christmas",
    "vancouver-holiday-market": "https://www.facebook.com/search/events/?q=Vancouver%20holiday%20market",
    "vancouver-santa-parade": "https://www.facebook.com/search/events/?q=Vancouver%20Santa%20Claus%20Parade",
    "edmonton-christmas": "https://www.facebook.com/search/events/?q=Edmonton%20Christmas",
    "edmonton-holiday-market": "https://www.facebook.com/search/events/?q=Edmonton%20holiday%20market",
    "london-christmas": "https://www.facebook.com/search/events/?q=London%20Christmas",
}

async def main():
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        ctx = await new_stealth_context(browser, settings, city_slug="toronto")
        page = await ctx.new_page()
        for slug, url in QUERIES.items():
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(6000)
                text = await page.evaluate("() => document.body.innerText")
                (OUT / (slug + ".txt")).write_text(text, encoding="utf-8")
                links = await page.evaluate("() => [...document.querySelectorAll('a[href*=\"/events/\"]')].map(a => a.href).filter((v,i,s) => s.indexOf(v)===i).slice(0,40)")
                print(slug + ": status=" + str(resp.status if resp else 0) + " textlen=" + str(len(text)) + " eventlinks=" + str(len(links)), flush=True)
                print("  HEAD:", text[:300].replace("\n", " | "), flush=True)
                print("  LINKS:", json.dumps(links, ensure_ascii=False), flush=True)
            except Exception as e:
                print(slug + ": FAILED " + str(e), flush=True)
            await asyncio.sleep(2)
        await ctx.close()
        await browser.close()

asyncio.run(main())
print("DONE")
