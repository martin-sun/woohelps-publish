import asyncio, sys
from pathlib import Path
sys.path.insert(0, "/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

ROOT = Path("/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
OUT = ROOT / "data/pending/2026-11-23"
BASE = "https://www.todocanada.ca/wp-content/uploads/"

JOBS = [
    ("tor-ice-skating", "ice-skating.png", "https://www.todocanada.ca/city/toronto/event/nathan-phillips-square-ice-skating-winter-programming/",
     [BASE + "Nathan-Phillips-Square-skating-rink.png"]),
    ("tor-etobicoke-parade", "parade.jpg", "https://www.todocanada.ca/city/toronto/event/etobicoke-lakeshore-santa-claus-parade/",
     [BASE + "20900733_844013519093902_8525244726840438778_o.jpg"]),
    ("tor-made-market", "made-market.jpg", "https://www.todocanada.ca/city/toronto/event/toronto-made-holiday-market/",
     [BASE + "TorontoHolidayMarket-855x570.jpg"]),
    ("tor-barkada", "barkada.png", "https://www.todocanada.ca/city/toronto/event/barkada-holiday-market-3/",
     [BASE + "HOLIDAYMARKETDEC6-855x570.png"]),
    ("reg-winter-wonderland", "winter-wonderland.webp", "https://www.todocanada.ca/city/regina/event/conexus-arts-centre-christmas-wonderland-viewing/",
     [BASE + "1080x1080_WinterSponsor_2611104-1024x1024.jpg-855x570.webp"]),
    ("reg-holiday-edit", "holiday-edit.webp", "https://www.todocanada.ca/city/regina/event/mrkt-the-holiday-edit/",
     [BASE + "MRKT-The-Holiday-Edit.webp"]),
    ("hal-nutcracker", "nutcracker.jpg", "https://symphonynovascotia.ca/concerts-and-tickets/concerts/rebecca-cohn-auditorium/the-nutcracker-2026/",
     ["https://symphonynovascotia.ca/wp-content/uploads/2026-12-04-The-Nutcracker-v1-2026-27-Concert-Page-Links-800x600-high-res.jpg"]),
    ("hal-neptune-carol", "neptune-theatre.webp", "https://www.neptunetheatre.com/box-office/dickens-a-christmas-carol",
     ["https://novascotia.com/wp-content/uploads/2024/09/neptune-out-1920x1080-66f76b8a05e04.webp"]),
]

async def main():
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        ctx = await new_stealth_context(browser, settings, city_slug="toronto")
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
