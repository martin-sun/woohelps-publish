"""活动发布命令行：读一个活动稿 JSON，经项目发布通道发到平台活动系统并回读核验。

用法：
    uv run python scripts/publish_activity_draft.py --input draft.json [--dry-run]

稿件 JSON 字段见 data/pending 下样例：title_zh、title_en、description_zh、
content_zh、address、venue_name、price、is_free、fee_amount、start_time_local、
end_time_local、timezone、image_urls、source、source_url、city_slug、activity_type。

时间口径：平台按本地墙钟时间存储与展示（已用 2025 年魔法森林的平台记录核对：
存入的开始时刻 17:30 即本地开始时间），故 start_time_local 原样提交、不做 UTC 换算。

输出 JSON：真实发布时含 activity_id 与回读核验（名称、开始时间、地址）。
"""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.models.activity import ProcessedActivity
from src.publisher.woohelps import WoohelpsPublisher

FMT = "%Y-%m-%d %H:%M"
CITY_ZH = {"saskatoon": "萨斯卡通", "toronto": "多伦多", "vancouver": "温哥华", "calgary": "卡尔加里"}
CITY_FALLBACK = {"saskatoon": 6}


def build_activity(d):
    start = datetime.strptime(d["start_time_local"], FMT)
    end = datetime.strptime(d["end_time_local"], FMT) if d.get("end_time_local") else None
    raw = "%s|%s|%s" % (d.get("title_en", ""), d["start_time_local"], d.get("address", ""))
    source_id = "%s#%s" % (d["source_url"], hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8])
    imgs = list(d.get("image_urls") or [])
    return ProcessedActivity(
        source=d.get("source", "todocanada"),
        source_id=source_id,
        source_url=d["source_url"],
        city_slug=d.get("city_slug", "saskatoon"),
        title_en=d.get("title_en", ""),
        title_zh=d["title_zh"],
        description_zh=d.get("description_zh", ""),
        content_zh=d["content_zh"],
        address=d["address"],
        venue_name=d.get("venue_name"),
        price=d.get("price"),
        is_free=bool(d.get("is_free")),
        fee_amount=float(d.get("fee_amount") or 0),
        fee_parsed_free=bool(d.get("is_free")),
        start_time_utc=start,
        end_time_utc=end,
        timezone=d.get("timezone"),
        image_url=imgs[0] if imgs else None,
        image_urls=imgs,
        activity_type=int(d.get("activity_type", 1)),
    )


async def resolve_images(pub, d):
    from pathlib import PurePosixPath
    resolved, need_browser = [], []
    for u in d.get("image_urls") or []:
        up = await pub.upload_image(u)
        if up:
            resolved.append(up)
        else:
            need_browser.append(u)
    if need_browser:
        from playwright.async_api import async_playwright
        from src.scrapers.browser import launch_browser, new_stealth_context
        settings = get_settings()
        async with async_playwright() as p:
            browser = await launch_browser(p, settings)
            ctx = await new_stealth_context(browser, settings, city_slug=d.get("city_slug", "saskatoon"))
            page = await ctx.new_page()
            for u in need_browser:
                resp = await page.goto(u)
                if resp and resp.ok:
                    body = await resp.body()
                    name = PurePosixPath(u).name.split("?")[0] or "image.jpg"
                    up = await pub.upload_image_bytes(body, name)
                    if up:
                        resolved.append(up)
                await asyncio.sleep(1)
            await ctx.close()
            await browser.close()
    return resolved


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    d = json.loads(Path(args.input).read_text(encoding="utf-8"))
    act = build_activity(d)
    if args.dry_run:
        print(json.dumps({
            "dry_run": True, "name": act.title_zh,
            "start_time": act.start_time_utc.strftime(FMT),
            "end_time": act.end_time_utc.strftime(FMT) if act.end_time_utc else "",
            "address": act.address,
            "fee_type": 1 if act.fee_parsed_free else 2,
            "fee": act.fee_amount, "images": act.image_urls,
        }, ensure_ascii=False, indent=2))
        return 0
    settings = get_settings()
    async with WoohelpsPublisher(base_url=settings.WOOHELPS_API_URL, login_session=settings.WOOHELPS_LOGIN_SESSION) as pub:
        await pub.fetch_city_mapping()
        city_id = pub.get_city_id(CITY_ZH.get(act.city_slug, "")) or CITY_FALLBACK.get(act.city_slug)
        if not city_id:
            print(json.dumps({"ok": False, "error": "no city_id for " + act.city_slug}, ensure_ascii=False))
            return 1
        imgs = await resolve_images(pub, d)
        if not imgs:
            print(json.dumps({"ok": False, "error": "all image uploads failed"}, ensure_ascii=False))
            return 1
        act.image_url = imgs[0]
        act.image_urls = imgs
        result = await pub.publish_activity(act, city_id)
        out = {"publish_result": result}
        aid = None
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict):
            aid = data.get("id") or data.get("activity_id")
        if aid is None and isinstance(result, dict):
            aid = result.get("id") or result.get("activity_id")
        out["activity_id"] = aid
        if aid:
            try:
                r = await pub.client.get(
                    settings.WOOHELPS_API_URL + "/api/applet/activity/info/get",
                    params={"activity_id": aid},
                    headers={"LOGIN-SESSION": settings.WOOHELPS_LOGIN_SESSION},
                )
                info = r.json()
                ad = info.get("data") or info
                out["verify"] = {"errcode": info.get("errcode"), "name": ad.get("name"), "start_time": ad.get("start_time"), "address": ad.get("address")}
            except Exception as e:
                out["verify"] = {"error": type(e).__name__}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        ok = isinstance(result, dict) and result.get("errcode") in (0, 101, 201)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
