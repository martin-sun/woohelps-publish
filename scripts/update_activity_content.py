"""更新已发布活动的正文（description）：按新格式重排并去掉来源行。

用法：
    uv run python scripts/update_activity_content.py --input draft.json --activity-id 5039 [--dry-run]

做法：先读平台当前记录，沿用其名称、时间、地址、费用、图片等全部字段，
只把 description 按稿件 content_zh 重建：去掉以“来源”开头的段落，
其余按空行拆成多个文本块（读者端段落分明）。更新后回读核验并输出结果。
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.publisher.woohelps import WoohelpsPublisher


def build_description(content):
    paras = []
    for p in content.split("\n\n"):
        p = p.strip()
        if not p or p.startswith("来源"):
            continue
        paras.append(p)
    desc = json.dumps(
        [{"type": 1, "text": json.dumps({"txt": p, "path": ""}, ensure_ascii=False)} for p in paras],
        ensure_ascii=False,
    )
    return desc, len(paras)


def wall(ep):
    return datetime.fromtimestamp(ep, timezone.utc).strftime("%Y-%m-%d %H:%M")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--activity-id", required=True, type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    d = json.loads(Path(args.input).read_text(encoding="utf-8"))
    desc, nblocks = build_description(d["content_zh"])
    settings = get_settings()
    async with WoohelpsPublisher(base_url=settings.WOOHELPS_API_URL, login_session=settings.WOOHELPS_LOGIN_SESSION) as pub:
        headers = {"LOGIN-SESSION": settings.WOOHELPS_LOGIN_SESSION}
        url = settings.WOOHELPS_API_URL + "/api/applet/activity/info/get"
        r = await pub.client.get(url, params={"activity_id": args.activity_id}, headers=headers)
        cur = r.json()
        imgs = cur.get("imgs") or []
        if isinstance(imgs, str):
            try:
                imgs = json.loads(imgs)
            except Exception:
                imgs = [cur.get("img")] if cur.get("img") else []
        payload = {
            "activity_id": args.activity_id,
            "name": cur.get("name"),
            "description": desc,
            "city_id": (cur.get("city") or {}).get("id", 6),
            "city_area_id": cur.get("city_area") or 0,
            "start_time": wall(cur.get("start_time")),
            "end_time": wall(cur.get("end_time")),
            "address": cur.get("address"),
            "img": cur.get("img"),
            "imgs": json.dumps(imgs, ensure_ascii=False),
            "fee_type": cur.get("fee_type"),
            "fee": cur.get("fee"),
            "enroll_type": cur.get("enroll_type", 1),
            "remind_type": cur.get("remind_type", 1),
            "groupon_type": cur.get("groupon_type", 1),
        }
        if args.dry_run:
            print(json.dumps({"dry_run": True, "activity_id": args.activity_id, "blocks": nblocks, "name": payload["name"]}, ensure_ascii=False))
            return 0
        resp = await pub.client.post(settings.WOOHELPS_API_URL + "/api/applet/activity/update", data=payload, headers=headers)
        try:
            result = resp.json()
        except Exception:
            result = {"status_code": resp.status_code, "text": resp.text[:200]}
        out = {"update_result": result}
        r2 = await pub.client.get(url, params={"activity_id": args.activity_id}, headers=headers)
        d2 = r2.json()
        try:
            blocks = json.loads(d2.get("description") or "[]")
            has_src = False
            for b in blocks:
                try:
                    txt = json.loads(b.get("text") or "{}").get("txt", "")
                except Exception:
                    txt = ""
                if txt.strip().startswith("来源"):
                    has_src = True
            out["verify"] = {"blocks": len(blocks), "has_source_line": has_src, "name": d2.get("name")}
        except Exception:
            out["verify"] = {"raw_desc_len": len(d2.get("description") or "")}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        ok = isinstance(result, dict) and result.get("errcode") in (0, 101, 201)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
