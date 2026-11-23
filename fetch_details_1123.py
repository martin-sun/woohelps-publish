import asyncio, json, re, sys
from pathlib import Path
sys.path.insert(0, "/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

ROOT = Path("/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
OUT = ROOT / "data/sweep/2026-11-23-activities/details"
OUT.mkdir(parents=True, exist_ok=True)
URLS = {
    "tor-ice-skating": "https://www.todocanada.ca/city/toronto/event/nathan-phillips-square-ice-skating-winter-programming/",
    "tor-fleurs": "https://www.todocanada.ca/city/toronto/event/fleurs-de-villes-noel-in-bloor-yorkville/",
    "tor-etobicoke-parade": "https://www.todocanada.ca/city/toronto/event/etobicoke-lakeshore-santa-claus-parade/",
    "tor-made-market": "https://www.todocanada.ca/city/toronto/event/toronto-made-holiday-market/",
    "tor-barkada": "https://www.todocanada.ca/city/toronto/event/barkada-holiday-market-3/",
    "tor-casa-loma": "https://www.todocanada.ca/city/toronto/event/magical-winterland-nights/",
    "reg-wonderful-life": "https://www.todocanada.ca/city/regina/event/its-a-wonderful-life-2/",
    "reg-winter-wonderland": "https://www.todocanada.ca/city/regina/event/conexus-arts-centre-christmas-wonderland-viewing/",
    "reg-santa-visit": "https://www.todocanada.ca/city/regina/event/visit-from-santa-claus/",
    "reg-holiday-edit": "https://www.todocanada.ca/city/regina/event/mrkt-the-holiday-edit/",
    "reg-ornaments": "https://www.todocanada.ca/city/regina/event/festive-wooden-ornaments/",
    "hal-ast-market": "https://www.todocanada.ca/city/halifax/event/christmas-makers-market-at-ast/",
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
        ctx = await new_stealth_context(browser, settings, city_slug="toronto")
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
