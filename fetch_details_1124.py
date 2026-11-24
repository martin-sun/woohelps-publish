import asyncio, json, re, sys
from pathlib import Path
sys.path.insert(0, "/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

ROOT = Path("/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
OUT = ROOT / "data/sweep/2026-11-24-activities/details"
OUT.mkdir(parents=True, exist_ok=True)
URLS = {
    "van-shipyards": "https://www.todocanada.ca/city/vancouver/event/shipyards-artisan-christmas-market/",
    "van-gingerbread": "https://www.todocanada.ca/city/vancouver/event/gingerbread-lane/",
    "van-fleurs": "https://www.todocanada.ca/city/vancouver/event/fleurs-de-villes-noel-a-socially-spaced-festive-floral-trail/",
    "van-makeit": "https://www.todocanada.ca/city/vancouver/event/make-it-vancouver/",
    "van-solstice": "https://www.todocanada.ca/city/vancouver/event/annual-winter-solstice-lantern-festival/",
    "van-japan-market": "https://www.todocanada.ca/city/vancouver/event/japan-market-christmas-fair/",
    "van-dundarave": "https://www.todocanada.ca/city/vancouver/event/dundarave-festival-of-lights/",
    "edm-enchanted": "https://www.todocanada.ca/city/edmonton/event/enchanted-forest/",
    "edm-streetcar": "https://www.todocanada.ca/city/edmonton/event/christmas-streetcar/",
    "edm-panto": "https://www.todocanada.ca/city/edmonton/event/jack-and-the-beanstalk-an-e-town-panto/",
    "edm-ets-tours": "https://www.todocanada.ca/city/edmonton/event/ets-christmas-light-tours/",
    "edm-newcomer": "https://www.todocanada.ca/city/edmonton/event/community-and-newcomer-night/",
    "edm-muttart": "https://www.todocanada.ca/city/edmonton/event/copy-candlelight-christmas/",
    "edm-rutherford": "https://www.todocanada.ca/city/edmonton/event/a-christmas-past-at-rutherford-house/",
    "lon-victoria": "https://www.todocanada.ca/city/london/event/winter-wonderland/",
    "lon-hyde-market": "https://www.todocanada.ca/city/london/event/hyde-park-christmas-market-fireworks/",
    "lon-argyle": "https://www.todocanada.ca/city/london/event/argyle-bia-santa-claus-parade/",
    "lon-winter-wonders": "https://www.todocanada.ca/city/london/event/winter-wonders-2/",
    "lon-wonderful-life": "https://www.todocanada.ca/city/london/event/its-a-wonderful-life/",
    "lon-rudolph": "https://www.todocanada.ca/city/london/event/rudolph-the-red-nosed-reindeer-jr/",
}

def parse_ld(html):
    events = []
    for m in re.finditer(r"<script[^>]*type=\"application/ld\+json\"[^>]*>(.*?)</script>", html, re.S | re.I):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "Event":
                events.append(it)
            elif isinstance(it, dict) and isinstance(it.get("@graph"), list):
                for g in it["@graph"]:
                    if isinstance(g, dict) and g.get("@type") == "Event":
                        events.append(g)
    return events

async def main():
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        ctx = await new_stealth_context(browser, settings, city_slug="vancouver")
        page = await ctx.new_page()
        for slug, url in URLS.items():
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                status = resp.status if resp else 0
            except Exception as e:
                print(slug, "FAILED", str(e)[:120], flush=True)
                continue
            html = await page.content()
            (OUT / (slug + ".html")).write_text(html, encoding="utf-8")
            ld = parse_ld(html)
            imgs = sorted(set(re.findall(r"<img[^>]+src=\"([^\"]+)\"", html)))
            imgs = [i for i in imgs if "todocanada" in i or "wp-content" in i]
            txt = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
            txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.S | re.I)
            txt = re.sub(r"<[^>]+>", " ", txt)
            txt = re.sub(r"\s+", " ", txt)
            summary = {"slug": slug, "url": url, "status": status, "ld": ld, "images": imgs[:12], "text": txt[:9000]}
            (OUT / (slug + ".json")).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
            nm = ld[0].get("name") if ld else "?"
            print(slug, status, "|", str(nm)[:55], "| imgs:", len(imgs), flush=True)
            await asyncio.sleep(1)
        await ctx.close()
        await browser.close()

asyncio.run(main())
print("DONE")
