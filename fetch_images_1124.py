import asyncio, sys
from pathlib import Path
sys.path.insert(0, "/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

ROOT = Path("/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
OUT = ROOT / "data/pending/2026-11-24"
BASE = "https://www.todocanada.ca/wp-content/uploads/"

JOBS = [
    ("van-gingerbread", "gingerbread.jpg", "https://www.todocanada.ca/city/vancouver/event/gingerbread-lane/",
     [BASE + "Gingerbread-Lane-main-photo1-855x570.jpg"]),
    ("van-makeit", "makeit.jpg", "https://www.todocanada.ca/city/vancouver/event/make-it-vancouver/",
     [BASE + "Make-It-The-Handmade-Revolution24.jpg"]),
    ("van-japan-market", "japan-market.png", "https://www.todocanada.ca/city/vancouver/event/japan-market-christmas-fair/",
     [BASE + "Christmas-Poster-Japan.png"]),
    ("van-solstice", "solstice.jpg", "https://www.todocanada.ca/city/vancouver/event/annual-winter-solstice-lantern-festival/",
     [BASE + "25487292_10156101138883982_1734758172969821392_o.jpg"]),
    ("edm-streetcar", "streetcar.jpg", "https://www.todocanada.ca/city/edmonton/event/christmas-streetcar/",
     [BASE + "D890C8D6-303D-4BA2-BF85-EC9328900AF7-e1730828931459-855x570.jpeg"]),
    ("edm-newcomer", "newcomer.jpg", "https://www.todocanada.ca/city/edmonton/event/community-and-newcomer-night/",
     [BASE + "CommunityAndNewcomerNight_1200w-855x570.jpg"]),
    ("edm-ets-tours", "ets-tours.jpg", "https://www.todocanada.ca/city/edmonton/event/ets-christmas-light-tours/",
     [BASE + "holiday_lights_tour.jpg"]),
    ("edm-rutherford", "rutherford.jpg", "https://www.todocanada.ca/city/edmonton/event/a-christmas-past-at-rutherford-house/",
     [BASE + "RH_Christmas-Past-2026_email-03_V1-855x570.jpg"]),
    ("edm-muttart", "muttart.jpg", "https://www.todocanada.ca/city/edmonton/event/copy-candlelight-christmas/",
     [BASE + "Candle-Box.jpg"]),
    ("lon-victoria", "victoria.jpg", "https://www.todocanada.ca/city/london/event/winter-wonderland/",
     [BASE + "Christmas-Day-London-1-855x570.jpg"]),
    ("lon-hyde-market", "hyde-market.png", "https://www.todocanada.ca/city/london/event/hyde-park-christmas-market-fireworks/",
     [BASE + "Tourism-London-Xmas-Market-2025-1.png"]),
    ("lon-argyle", "argyle.png", "https://www.todocanada.ca/city/london/event/argyle-bia-santa-claus-parade/",
     [BASE + "Argyle-BIA-Santa-Claus-Parade-2025-855x570.png"]),
    ("lon-rudolph", "rudolph.webp", "https://www.todocanada.ca/city/london/event/rudolph-the-red-nosed-reindeer-jr/",
     [BASE + "Rudolph-The-Red-Nosed-Reindeer-JR.webp"]),
    ("lon-winter-wonders", "winter-wonders.jpg", "https://www.todocanada.ca/city/london/event/winter-wonders-2/",
     [BASE + "469897497_994000172769335_6469416656544487901_n.jpg"]),
]

async def main():
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        ctx = await new_stealth_context(browser, settings, city_slug="vancouver")
        for slug, fname, ref, urls in JOBS:
            d = OUT / slug
            d.mkdir(parents=True, exist_ok=True)
            done = False
            for u in urls:
                try:
                    r = await ctx.request.get(u, headers={"Referer": ref})
                    if r.status == 200:
                        body = await r.body()
                        if len(body) > 5000:
                            (d / fname).write_bytes(body)
                            print(slug, "SAVED", fname, len(body), flush=True)
                            done = True
                            break
                        else:
                            print(slug, "too small", len(body), flush=True)
                    else:
                        print(slug, u[:60], "status", r.status, flush=True)
                except Exception as e:
                    print(slug, "ERR", str(e)[:120], flush=True)
            if not done:
                print(slug, "ALL FAILED", flush=True)
            await asyncio.sleep(1)
        await ctx.close()
        await browser.close()

asyncio.run(main())
print("DONE")
