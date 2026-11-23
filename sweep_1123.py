import asyncio, json, re, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, "/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

ROOT = Path("/Users/msun-2018/Projects/overseas-new-life/woohelps-publish")
OUT = ROOT / "data/sweep/2026-11-23-activities/tor-reg-hal"
OUT.mkdir(parents=True, exist_ok=True)
CITIES = ["toronto", "regina", "halifax"]
PROBE = ["toronto", "regina", "halifax"]
WIN_START = date(2026, 11, 25)
WIN_END = date(2026, 12, 23)
MAX_PAGES = 20

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

def norm(ev):
    loc = ev.get("location") or {}
    addr = loc.get("address") or {}
    if isinstance(addr, str):
        street, locality, region = addr, "", ""
    else:
        street = addr.get("streetAddress", "")
        locality = addr.get("addressLocality", "")
        region = addr.get("addressRegion", "")
    img = None
    if isinstance(ev.get("image"), str):
        img = ev["image"]
    elif isinstance(ev.get("image"), dict):
        img = ev["image"].get("url")
    elif isinstance(ev.get("image"), list) and ev["image"]:
        first = ev["image"][0]
        img = first if isinstance(first, str) else first.get("url")
    return {
        "name": (ev.get("name") or "").strip(),
        "url": ev.get("url") or "",
        "start": (ev.get("startDate") or "")[:10],
        "end": (ev.get("endDate") or "")[:10],
        "venue": (loc.get("name") or "").strip(),
        "street": (street or "").strip(),
        "locality": (locality or "").strip(),
        "region": (region or "").strip(),
        "image": img,
        "desc": (ev.get("description") or "")[:500],
    }

async def fetch_city(p, settings, slug, max_pages=MAX_PAGES):
    browser = await launch_browser(p, settings)
    ctx = await new_stealth_context(browser, settings, city_slug=slug)
    page = await ctx.new_page()
    pages_out = []
    for pn in range(max_pages):
        base = f"https://www.todocanada.ca/city/{slug}/events/page/{pn}/" if pn > 0 else f"https://www.todocanada.ca/city/{slug}/events/"
        url = base + "?etype=nextmonth&event_sortby=stdate_high_low"
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else 0
        except Exception as e:
            print(f"[{slug}] p{pn} FAILED {e}", flush=True)
            pages_out.append({"page": pn, "url": url, "status": 0, "error": str(e), "ld_count": 0, "ld": []})
            break
        html = await page.content()
        (OUT / f"{slug}-p{pn}.html").write_text(html, encoding="utf-8")
        ld = parse_ld(html)
        print(f"[{slug}] p{pn} status={status} ld_events={len(ld)}", flush=True)
        pages_out.append({"page": pn, "url": url, "status": status, "ld_count": len(ld), "ld": [norm(e) for e in ld]})
        if status in (404, 403):
            break
        starts = [(e.get("startDate") or "")[:10] for e in ld]
        starts = [s for s in starts if s]
        if starts and max(starts) < str(WIN_START):
            print(f"[{slug}] p{pn} newest start {max(starts)} < window start, stop", flush=True)
            break
        await asyncio.sleep(2)
    await ctx.close()
    await browser.close()
    (OUT / f"{slug}-list.json").write_text(json.dumps({"slug": slug, "pages": pages_out}, ensure_ascii=False, indent=1), encoding="utf-8")
    return pages_out

async def main():
    settings = get_settings()
    async with async_playwright() as p:
        for slug in PROBE:
            try:
                await fetch_city(p, settings, slug, max_pages=1)
            except Exception as e:
                print(f"[{slug}] PROBE ERROR {e}", flush=True)
        for slug in CITIES:
            try:
                await fetch_city(p, settings, slug)
            except Exception as e:
                print(f"[{slug}] ERROR {e}", flush=True)

asyncio.run(main())
print("DONE")
